"""Independent batch reference implementation.

The streaming pipeline and this reference deliberately take different paths
through the problem:

* the pipeline (:mod:`ndjson_pipe.importer`) is chunked, stateful and
  resumable, and offsets come from the streaming decoder;
* this reference reads the *whole sealed file at once*, walks it with a plain
  index loop and builds expected results in memory.

The two share only the documented rule layer
(:func:`ndjson_pipe.normalize.normalize_line`) -- the normalization rules are
the specification itself. Splitting, offset bookkeeping, batching and
idempotency are all implemented a second time here, so any transport-level
divergence is caught by the differential tests.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .decoder import RawLine
from .importer import content_hash
from .normalize import Normalized, Reject, normalize_line


@dataclass
class ExpectedOutcome:
    """Expected end state of one source after a complete replay."""

    source_id: str
    records: List[Tuple] = field(default_factory=list)
    rejections: List[Tuple] = field(default_factory=list)
    conflicts: List[Tuple] = field(default_factory=list)
    checkpoint: int = 0
    blank_lines: int = 0

    def summary(self) -> Dict[str, int]:
        return {
            "records": len(self.records),
            "rejections": len(self.rejections),
            "conflicts": len(self.conflicts),
            "checkpoint": self.checkpoint,
            "blank_lines": self.blank_lines,
        }


def batch_split_lines(data: bytes) -> List[RawLine]:
    """Batch line splitter: plain index walk, independent of the decoder."""
    lines: List[RawLine] = []
    line_no = 0
    cursor = 0
    n = len(data)
    while cursor < n:
        nl = data.find(b"\n", cursor)
        if nl == -1:
            start, end, cursor_next = cursor, n, n
        else:
            start, end, cursor_next = cursor, nl + 1, nl + 1
        content_end = nl if nl != -1 else n
        if content_end > start and data[content_end - 1] == 0x0D:
            content_end -= 1
        line_no += 1
        lines.append(RawLine(line_no, start, end, data[start:content_end]))
        cursor = cursor_next
    return lines


class _ReferenceState:
    """Accumulator shared by the single-pass and delivery-sequence models."""

    def __init__(self, source_id: str):
        self.outcome = ExpectedOutcome(source_id=source_id)
        # start_offset -> (kind, hash); the durable idempotency memory
        self.seen: Dict[int, Tuple[str, str]] = {}

    def absorb(self, raw_line: RawLine) -> None:
        # Checkpoints are monotonic: redeliveries never move them backwards.
        self.outcome.checkpoint = max(self.outcome.checkpoint, raw_line.end_offset)
        if not raw_line.raw:
            self.outcome.blank_lines += 1
            return

        digest = content_hash(raw_line.raw)
        prior = self.seen.get(raw_line.start_offset)
        if prior is not None:
            prior_kind, prior_hash = prior
            if prior_hash == digest:
                return  # idempotent replay, no new row
            self.outcome.conflicts.append(
                (raw_line.start_offset, prior_kind, prior_hash, digest)
            )
            # The position's identity stays the first durable content; the
            # conflict row itself records the incoming bytes, mirroring the
            # pipeline which never overwrites records/rejections.
            return

        try:
            normalized: Normalized = normalize_line(raw_line)
        except Reject as reject:
            self.seen[raw_line.start_offset] = ("rejection", digest)
            self.outcome.rejections.append(
                (
                    raw_line.line_no,
                    raw_line.start_offset,
                    raw_line.end_offset,
                    reject.reason,
                    reject.field,
                    digest,
                )
            )
            return

        self.seen[raw_line.start_offset] = ("record", digest)
        self.outcome.records.append(
            (
                raw_line.line_no,
                raw_line.start_offset,
                raw_line.end_offset,
                normalized.record_id,
                normalized.version,
                normalized.normalized_by,
                normalized.currency,
                str(normalized.amount),
                normalized.amount_cents,
                1 if normalized.note_present else 0,
                normalized.note_value,
                digest,
            )
        )


def expected_for_source(source_id: str, data: bytes) -> ExpectedOutcome:
    """Compute the expected outcome of a sealed source from scratch."""
    state = _ReferenceState(source_id)
    for raw_line in batch_split_lines(data):
        state.absorb(raw_line)
    return state.outcome


def expected_for_append_sequence(source_id: str, parts: List[bytes]) -> ExpectedOutcome:
    """Expected outcome when the source was revealed through byte ``parts``.

    The logical source is the concatenation, so the expectation is identical
    to a single full-file pass -- this function exists mainly to make that
    invariant explicit in the tests.
    """
    return expected_for_source(source_id, b"".join(parts))


def expected_for_deliveries(source_id: str, deliveries: List[bytes]) -> ExpectedOutcome:
    """Expected outcome of repeated full redeliveries of one logical source.

    Each delivery is an independent file scanned from byte 0 against the same
    offset space (the pipeline's ``full_scan`` mode): identical content at a
    previously seen position is idempotent, different content becomes a
    conflict. Deliveries do not concatenate -- every one restarts at offset 0
    and its physical line numbers restart at 1.
    """
    state = _ReferenceState(source_id)
    for data in deliveries:
        for raw_line in batch_split_lines(data):
            state.absorb(raw_line)
    return state.outcome
