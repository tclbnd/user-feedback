#!/usr/bin/env python3
"""Checkpointed staging log with safe segment truncation and recovery.

Input/output contract (see README.md)::

    python3 wal.py input.json output.json

The crash and corruption operations are modelled deterministically so the
staging-log state machine can be exercised without a real filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from typing import Any, Dict, Iterable, List, Set

BAD = "<BAD>"


def _sorted_batches(batch_ids: Iterable[Any]) -> List[Any]:
    """Return batch ids in a stable, JSON-friendly order."""
    values = set(batch_ids)
    try:
        return sorted(values)
    except TypeError:  # mixed JSON types are not mutually orderable
        return sorted(values, key=lambda value: (type(value).__name__, str(value)))


class SegmentLog:
    """Append-only staging log split into fixed-size segments.

    Invariants
    ----------
    * ``seq`` is monotonic and never reused, even after truncation.
    * ``checkpoint`` is a durable watermark.  A record is committed (and may
      be ingested) exactly when ``seq <= checkpoint``.
    * A live segment is only reclaimed when *all* of its records are at or
      below the watermark.  A segment that straddles the watermark stays in
      the log in full.
    * Damage is tracked per record (``record["bad"]``); replay skips that
      slot and continues with the records after it.
    * Destination ingestion is idempotent and keyed by ``seq``.  It does not
      depend on a segment still being present, so truncation cannot erase
      already-committed batches from the recovery summary.

    Every state transition is protected by an ``RLock``.  Appenders and the
    truncator can therefore run concurrently: a truncation publishes a new
    segment list with one assignment, and an appender never gets a reference
    to a segment that has been reclaimed.
    """

    def __init__(self, seg_size: int = 8) -> None:
        if isinstance(seg_size, bool) or not isinstance(seg_size, int) or seg_size <= 0:
            raise ValueError("seg_size must be a positive integer")
        self.seg_size = seg_size
        self._lock = threading.RLock()
        self._segments: List[List[Dict[str, Any]]] = []
        self._next_seq = 0
        self._checkpoint = 0
        # Durable destination ledger.  It survives crashes and truncation.
        self._dest_seqs: Set[int] = set()
        self._dest_batches: Set[Any] = set()
        # Durable, compact index of every record ever appended:
        # ``{"seq": n, "batch": b}``.  Logical corruption addressing
        # (``batch``/``index``, ``seq``) resolves against this, so a fault
        # targets the same record regardless of segment layout or how much
        # of the log has already been reclaimed.
        self._log_index: List[Dict[str, Any]] = []
        # Records known to be damaged.  Kept as a seq set so damage stays
        # meaningful even if the record's segment is reclaimed later.
        self._bad_seqs: Set[int] = set()
        # Committed records evicted by truncation before an explicit ingest.
        # Keeping them makes "checkpoint, truncate, ingest" lossless while
        # still allowing the live segment summary to shrink.
        self._pending_committed: List[Dict[str, Any]] = []
        self._stats = {
            "segments_created": 0,
            "segments_truncated": 0,
            "records_appended": 0,
            "records_truncated": 0,
            "records_corrupted": 0,
            "checkpoints": 0,
            "crashes": 0,
            "recoveries": 0,
            # High-water mark of every record ever reclaimed.  A correct
            # implementation keeps this <= the checkpoint that justified
            # the truncation.
            "max_truncated_seq": 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers; callers must hold self._lock.
    # ------------------------------------------------------------------
    def _append_one_locked(self, batch: Any, value: Any) -> Dict[str, Any]:
        if not self._segments or len(self._segments[-1]) >= self.seg_size:
            self._segments.append([])
            self._stats["segments_created"] += 1
        self._next_seq += 1
        record = {"seq": self._next_seq, "batch": batch, "r": value, "bad": False}
        self._segments[-1].append(record)
        self._log_index.append({"seq": record["seq"], "batch": batch})
        self._stats["records_appended"] += 1
        return record

    def _is_ingested(self, record: Dict[str, Any]) -> bool:
        return record["seq"] in self._dest_seqs

    def _is_damaged(self, record: Dict[str, Any]) -> bool:
        # ``bad`` is set on live records by the ``corrupt`` operation; the
        # seq set is authoritative because it outlives segment reclamation.
        # The ``<BAD>`` payload sentinel is also recognised so an input that
        # models damage directly is handled the same way.
        return (
            record["seq"] in self._bad_seqs
            or bool(record.get("bad"))
            or record.get("r") == BAD
        )

    def _ingest_record_locked(self, record: Dict[str, Any]) -> bool:
        """Apply one record to the destination at most once."""
        if self._is_damaged(record):
            return False
        seq = record["seq"]
        if seq in self._dest_seqs:
            return False
        self._dest_seqs.add(seq)
        self._dest_batches.add(record["batch"])
        return True

    def _replay_locked(self, upto: int) -> int:
        """Apply valid records with seq <= upto, in seq order."""
        candidates = list(self._pending_committed)
        for segment in self._segments:
            candidates.extend(segment)
        applied = 0
        seen = set()
        for record in sorted(candidates, key=lambda item: item["seq"]):
            if record["seq"] in seen:
                continue
            seen.add(record["seq"])
            if record["seq"] <= upto and self._ingest_record_locked(record):
                applied += 1
        # Evicted-but-committed entries are only kept until they reach the
        # destination; once there, the ledger is authoritative and the copy
        # would otherwise grow without bound.  Damaged entries can never be
        # applied, so they are dropped too.
        if self._pending_committed:
            self._pending_committed = [
                record
                for record in self._pending_committed
                if record["seq"] not in self._dest_seqs and not self._is_damaged(record)
            ]
        return applied

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def append(self, batch: Any, records: Iterable[Any]) -> List[Dict[str, Any]]:
        """Append one batch atomically, splitting it across segments."""
        with self._lock:
            return [self._append_one_locked(batch, value) for value in records]

    def checkpoint(self) -> int:
        """Publish the current high-water mark and return it."""
        with self._lock:
            self._checkpoint = self._next_seq
            self._stats["checkpoints"] += 1
            return self._checkpoint

    def truncate(self) -> int:
        """Reclaim only segments whose every record is checkpointed.

        Returns the number of reclaimed segments.  A segment with even one
        record above the watermark is retained in full; this is the fix for
        deleting a segment merely because its *first* record was below the
        watermark.
        """
        with self._lock:
            keep: List[List[Dict[str, Any]]] = []
            removed = 0
            removed_records = 0
            for segment in self._segments:
                fully_committed = not segment or all(
                    record["seq"] <= self._checkpoint for record in segment
                )
                if not fully_committed:
                    keep.append(segment)
                    continue

                removed += 1
                removed_records += len(segment)
                if segment:
                    self._stats["max_truncated_seq"] = max(
                        self._stats["max_truncated_seq"],
                        segment[-1]["seq"],
                    )
                # Preserve valid, not-yet-ingested records across the
                # eviction.  Normally ingest happens before truncate; this
                # makes the opposite order lossless too.
                for record in segment:
                    if (
                        not self._is_damaged(record)
                        and record["seq"] <= self._checkpoint
                        and not self._is_ingested(record)
                    ):
                        self._pending_committed.append(record)

            # Publish atomically.  Concurrent appenders observe either the
            # old or the new list, never a partially filtered one.
            self._segments = keep
            self._stats["segments_truncated"] += removed
            self._stats["records_truncated"] += removed_records
            return removed

    def corrupt(self, seg: int = 0, pos: int = 0, **address: Any) -> bool:
        """Damage a single record, leaving the rest of the segment readable.

        A record can be addressed either physically with ``seg``/``pos``
        (offsets into the live segment list, Python sequence semantics) or
        logically with ``batch``/``index`` (the n-th record of that batch)
        or ``seq``.  Logical addressing is layout independent, which makes
        fault scenarios reproducible across different ``seg_size`` values.

        Returns whether a record was actually damaged.
        """
        with self._lock:
            record = self._resolve_record_locked(seg, pos, address)
            if record is None:
                return False
            seq = record["seq"]
            if seq not in self._bad_seqs:
                self._bad_seqs.add(seq)
                self._stats["records_corrupted"] += 1
            # Mark the physical copy if it is still resident, so introspection
            # (and the segment payload itself) shows the damage.
            for live in self._all_records_locked():
                if live["seq"] == seq:
                    live["bad"] = True
                    live["r"] = BAD
                    break
            return True

    def _resolve_record_locked(self, seg: Any, pos: Any, address: Dict[str, Any]):
        # Logical addressing resolves against the full append history, so
        # the same fault hits the same record under any ``seg_size`` and
        # even after earlier records have been reclaimed.
        if address.get("seq") is not None:
            try:
                wanted = int(address["seq"])
            except (TypeError, ValueError):
                return None
            for entry in self._log_index:
                if entry["seq"] == wanted:
                    return entry
            return None
        if "batch" in address and address.get("batch") is not None:
            index = address.get("index", pos)
            try:
                index = int(index)
            except (TypeError, ValueError):
                return None
            matches = [
                entry for entry in self._log_index
                if entry["batch"] == address["batch"]
            ]
            if index < 0:
                index += len(matches)
            if 0 <= index < len(matches):
                return matches[index]
            return None
        # Physical addressing: offsets into the live segment list.
        try:
            seg = int(seg)
            pos = int(pos)
        except (TypeError, ValueError):
            return None
        if seg < 0:
            seg += len(self._segments)
        if seg < 0 or seg >= len(self._segments):
            return None
        segment = self._segments[seg]
        if pos < 0:
            pos += len(segment)
        if pos < 0 or pos >= len(segment):
            return None
        return segment[pos]

    def _all_records_locked(self):
        """Return every record still reachable, in ``seq`` order.

        Used by introspection and tests; intentionally independent from the
        append index so it never hides a reclaimed-but-pending record.
        """
        records = []
        for segment in self._segments:
            records.extend(segment)
        records.extend(self._pending_committed)
        records.sort(key=lambda item: item["seq"])
        return records

    def ingest(self) -> int:
        """Ingest every valid record at or below the checkpoint watermark.

        Records above the watermark remain staging-only and therefore are
        not visible to the warehouse.  Returns the number of newly applied
        records (duplicates are suppressed).
        """
        with self._lock:
            return self._replay_locked(self._checkpoint)

    def crash(self) -> None:
        """Simulate a process crash.

        Staging segments, the checkpoint watermark and the destination
        ledger are durable and survive.  Any volatile in-flight state is
        deliberately absent from this model; ``recover`` re-derives it.
        Records above the checkpoint remain on disk but are invisible until
        a later checkpoint commits them -- that is the "last consistent
        state" recovery replays to.
        """
        with self._lock:
            self._stats["crashes"] += 1

    def recover(self) -> int:
        """Replay the log up to the last consistent checkpoint.

        Replay is record-oriented: a damaged record is skipped on its own
        and valid records before *and after* it are still applied.  The
        destination ledger makes repeated recoveries idempotent.  Returns
        the number of newly applied records in this pass.
        """
        with self._lock:
            applied = self._replay_locked(self._checkpoint)
            self._stats["recoveries"] += 1
            return applied

    # ------------------------------------------------------------------
    # Introspection and output
    # ------------------------------------------------------------------
    def segments_summary(self) -> List[Dict[str, int]]:
        """Describe the live segments (``first``/``last`` seq, records)."""
        with self._lock:
            summary = []
            for segment in self._segments:
                if not segment:
                    continue
                summary.append(
                    {
                        "first": segment[0]["seq"],
                        "last": segment[-1]["seq"],
                        "records": len(segment),
                    }
                )
            return summary

    def lifecycle_stats(self) -> Dict[str, int]:
        """Return counters used by the acceptance checks/tests."""
        with self._lock:
            stats = dict(self._stats)
            stats["segments_live"] = len([s for s in self._segments if s])
            stats["checkpoint"] = self._checkpoint
            stats["records_ingested"] = len(self._dest_seqs)
            stats["batches_ingested"] = len(self._dest_batches)
            return stats

    def result(self, include_stats: bool = False) -> Dict[str, Any]:
        """Build the documented output document.

        The four documented keys are always present.  ``include_stats``
        additionally reports the segment lifecycle counters and the recovery
        point (the checkpoint watermark that recovery would replay to).
        """
        with self._lock:
            batches = _sorted_batches(self._dest_batches)
            document = {
                "ingested": batches,
                "segments": self.segments_summary(),
                # Recovery point / replay summary: number of distinct
                # records that reached the warehouse, independent of how
                # many times ingest/recover was invoked.
                "recovered": len(self._dest_seqs),
                "digest": hashlib.sha1(str(batches).encode("utf-8")).hexdigest(),
            }
            if include_stats:
                stats = self.lifecycle_stats()
                document["recovery_point"] = stats["checkpoint"]
                document["lifecycle"] = {
                    key: value for key, value in stats.items()
                    if key not in ("checkpoint",)
                }
            return document


# ----------------------------------------------------------------------
# JSON operation driver
# ----------------------------------------------------------------------
def _op_append(log: SegmentLog, op: Dict[str, Any]) -> None:
    records = op.get("records") or []
    if not isinstance(records, (list, tuple)):
        raise ValueError("append.records must be a JSON array")
    log.append(op.get("batch"), records)


def _op_checkpoint(log: SegmentLog, op: Dict[str, Any]) -> None:
    log.checkpoint()


def _op_truncate(log: SegmentLog, op: Dict[str, Any]) -> None:
    log.truncate()


def _op_corrupt(log: SegmentLog, op: Dict[str, Any]) -> None:
    address = {
        key: op[key]
        for key in ("seq", "batch", "index")
        if key in op
    }
    log.corrupt(op.get("seg", 0), op.get("pos", 0), **address)


def _op_ingest(log: SegmentLog, op: Dict[str, Any]) -> None:
    log.ingest()


def _op_crash(log: SegmentLog, op: Dict[str, Any]) -> None:
    log.crash()


def _op_recover(log: SegmentLog, op: Dict[str, Any]) -> None:
    log.recover()


_OPS = {
    "append": _op_append,
    "checkpoint": _op_checkpoint,
    "truncate": _op_truncate,
    "corrupt": _op_corrupt,
    "ingest": _op_ingest,
    "crash": _op_crash,
    "recover": _op_recover,
}


def run(request: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an input document and return the output document."""
    if not isinstance(request, dict):
        raise ValueError("input must be a JSON object")
    ops = request.get("ops") or []
    if not isinstance(ops, (list, tuple)):
        raise ValueError("ops must be a JSON array")
    log = SegmentLog(request.get("seg_size", 8))
    for op in ops:
        if not isinstance(op, dict):
            continue
        handler = _OPS.get(op.get("op"))
        if handler is not None:
            handler(log, op)
        # Unknown operations are ignored so future inputs remain readable.
    return log.result(include_stats=bool(request.get("include_stats", False)))


def main(argv: List[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stats_flag = "--stats" in argv
    argv = [arg for arg in argv if arg != "--stats"]
    if len(argv) != 2:
        sys.stderr.write("usage: python3 wal.py [--stats] input.json output.json\n")
        return 2
    input_path, output_path = argv
    try:
        with open(input_path, "r", encoding="utf-8") as handle:
            request = json.load(handle)
        if stats_flag:
            request["include_stats"] = True
        result = run(request)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        sys.stderr.write("wal: %s\n" % exc)
        return 1
    # Write atomically so a crash in the harness cannot leave a half-written
    # output document behind.
    directory = os.path.dirname(os.path.abspath(output_path)) or "."
    tmp_path = os.path.join(
        directory, "." + os.path.basename(output_path) + ".tmp." + str(os.getpid())
    )
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

