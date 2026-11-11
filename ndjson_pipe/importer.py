"""Resumable import orchestration.

One call processes a single NDJSON source (a shard or an appended shard set)
and guarantees:

* streaming, bounded-memory decoding -- only the residual line and the current
  transaction batch are held, never the whole file;
* records, rejections, conflicts and the checkpoint commit in one transaction;
* byte-offset idempotency -- replaying an already processed source position
  with identical content is a no-op, while different content at the same
  position is recorded as a conflict instead of overwriting history;
* crash injection at three deterministic points (used by acceptance tests):
  after parsing but before any persistence, inside the write transaction
  before commit, and immediately after commit.
"""

import hashlib
import os
from dataclasses import dataclass, field
from typing import List, Optional

from .decoder import NDJsonStreamDecoder, RawLine
from .normalize import Normalized, Reject, normalize_line
from .store import Store

# Crash injection points, selected by the PIPE_CRASH_POINT environment variable.
CRASH_BEFORE_PERSIST = "before_persist"
CRASH_IN_TRANSACTION = "in_transaction"
CRASH_AFTER_COMMIT = "after_commit"
CRASH_EXIT_CODE = 42


@dataclass
class CrashPlan:
    """Deterministic self-kill plan for recovery acceptance tests."""

    point: Optional[str] = None
    line_no: int = 2
    triggered: bool = False

    @classmethod
    def from_env(cls) -> "CrashPlan":
        point = os.environ.get("PIPE_CRASH_POINT")
        line_no = int(os.environ.get("PIPE_CRASH_LINE", "2"))
        if point in ("", "none", "__none__", "off"):
            point = None
        if point and point not in (CRASH_BEFORE_PERSIST, CRASH_IN_TRANSACTION, CRASH_AFTER_COMMIT):
            raise ValueError(f"unknown PIPE_CRASH_POINT {point!r}")
        return cls(point=point, line_no=line_no)

    def fire_if(self, point: str, line_no: Optional[int] = None) -> None:
        """Kill the process when this point matches the configured plan.

        os._exit is used deliberately: no finally blocks, no buffers and no
        atexit handlers run -- it simulates a hard process termination.
        """
        if self.point == point and not self.triggered and (line_no is None or line_no == self.line_no):
            self.triggered = True
            os._exit(CRASH_EXIT_CODE)


@dataclass
class ImportStats:
    source_id: str
    accepted: int = 0
    rejected: int = 0
    conflicts: int = 0
    replayed_identical: int = 0
    blank_lines: int = 0
    residual_bytes: int = 0
    checkpoint: int = 0
    rejection_breakdown: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "conflicts": self.conflicts,
            "replayed_identical": self.replayed_identical,
            "blank_lines": self.blank_lines,
            "residual_bytes": self.residual_bytes,
            "checkpoint": self.checkpoint,
            "rejection_breakdown": dict(sorted(self.rejection_breakdown.items())),
        }


def content_hash(raw: bytes) -> str:
    """Stable content identity of a line (terminator independent)."""
    return hashlib.sha256(raw).hexdigest()


@dataclass
class _PendingOp:
    kind: str  # 'record' | 'rejection' | 'conflict'
    raw_line: RawLine
    digest: str
    payload: object = None


def import_source(
    store: Store,
    source_id: str,
    path: str,
    *,
    sealed: bool = True,
    chunk_size: int = 64 * 1024,
    batch_size: int = 200,
    full_scan: bool = False,
    crash_plan: Optional[CrashPlan] = None,
    chunk_sizes: Optional[List[int]] = None,
) -> ImportStats:
    """Ingest one source file and return cumulative statistics.

    ``chunk_sizes`` (test hook) overrides the read size per call in order;
    when exhausted the fixed ``chunk_size`` is used. Production callers leave
    it unset and take the same code path with one constant size.
    """
    crash_plan = crash_plan or CrashPlan()
    stats = ImportStats(source_id=source_id)
    if full_scan:
        start_offset, start_line_no = 0, 0
    else:
        start_offset, start_line_no = store.get_checkpoint_ex(source_id)
    stats.checkpoint = start_offset

    decoder = NDJsonStreamDecoder(start_offset=start_offset, start_line_no=start_line_no)
    pending: List[_PendingOp] = []
    sizes = list(chunk_sizes) if chunk_sizes else []

    with open(path, "rb") as fh:
        if start_offset:
            fh.seek(start_offset)
        while True:
            read_size = sizes.pop(0) if sizes else chunk_size
            chunk = fh.read(read_size)
            if not chunk:
                break
            for raw_line in decoder.feed(chunk):
                _process_line(store, source_id, raw_line, pending, stats, crash_plan)
                if len(pending) >= batch_size:
                    _flush(store, source_id, pending, stats, crash_plan)
        for raw_line in decoder.flush(sealed=sealed):
            _process_line(store, source_id, raw_line, pending, stats, crash_plan)

    if pending:
        _flush(store, source_id, pending, stats, crash_plan)

    stats.residual_bytes = decoder.buffered_bytes
    # The residual partial line is never durable. Every complete line already
    # advanced the checkpoint inside its batch transaction, and when nothing
    # complete arrived the checkpoint already equals the resume position, so
    # the durable checkpoint is exactly the residual start in both cases.
    stats.checkpoint = store.get_checkpoint(source_id)
    if not sealed and decoder.buffered_bytes:
        assert stats.checkpoint == decoder.position, (
            f"checkpoint {stats.checkpoint} must pin residual at {decoder.position}"
        )
    return stats


def _process_line(
    store: Store,
    source_id: str,
    raw_line: RawLine,
    pending: List[_PendingOp],
    stats: ImportStats,
    crash_plan: CrashPlan,
) -> None:
    # Blank physical lines carry no record but are still processed bytes:
    # they must advance the checkpoint inside the same transaction machinery.
    if not raw_line.raw:
        stats.blank_lines += 1
        pending.append(_PendingOp("blank", raw_line, "", None))
        return

    digest = content_hash(raw_line.content_hash_input)
    existing = store.existing_at(source_id, raw_line.start_offset)
    if existing is not None:
        existing_kind, existing_hash = existing
        if existing_hash == digest:
            stats.replayed_identical += 1  # idempotent replay of the same bytes
            return
        stats.conflicts += 1
        pending.append(_PendingOp("conflict", raw_line, digest, (existing_kind, existing_hash)))
        return

    # Parse first; the before-persist crash happens after a successful parse.
    try:
        normalized: Normalized = normalize_line(raw_line)
    except Reject as reject:
        crash_plan.fire_if(CRASH_BEFORE_PERSIST, raw_line.line_no)
        stats.rejected += 1
        stats.rejection_breakdown[reject.reason] = stats.rejection_breakdown.get(reject.reason, 0) + 1
        pending.append(_PendingOp("rejection", raw_line, digest, reject))
        return

    crash_plan.fire_if(CRASH_BEFORE_PERSIST, raw_line.line_no)
    stats.accepted += 1
    pending.append(_PendingOp("record", raw_line, digest, normalized))


def _flush(
    store: Store,
    source_id: str,
    pending: List[_PendingOp],
    stats: ImportStats,
    crash_plan: CrashPlan,
) -> None:
    target_in_batch = any(op.raw_line.line_no == crash_plan.line_no for op in pending)
    store.begin()
    for op in pending:
        if op.kind == "record":
            store.insert_record(source_id, op.raw_line, op.digest, op.payload)
        elif op.kind == "rejection":
            store.insert_rejection(source_id, op.raw_line, op.digest, op.payload)
        elif op.kind == "conflict":
            existing_kind, existing_hash = op.payload
            store.insert_conflict(source_id, op.raw_line, existing_kind, existing_hash, op.digest)
        # 'blank' rows only move the checkpoint below
        store.advance_checkpoint(source_id, op.raw_line.end_offset, op.raw_line.line_no)

    if target_in_batch:
        crash_plan.fire_if(CRASH_IN_TRANSACTION)
    store.commit()
    if target_in_batch:
        crash_plan.fire_if(CRASH_AFTER_COMMIT)
    pending.clear()
