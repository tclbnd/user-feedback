"""Archived log segments `wal/<first>-<last>.jsonl` read as one stream.

The stream must have consecutive LSNs (also across segment files) and strictly
increasing record times.  `scan` stops at the first place where that is not
true and reports it as a gap: nothing after a gap can be trusted for replay.
"""
import json
import os
import re

_SEGMENT = re.compile(r"^(\d{8})-(\d{8})\.jsonl$")
_TYPES = ("BEGIN", "PUT", "DEL", "COMMIT", "ABORT")


class Gap:
    """First untrustworthy position of the archive and the last good record."""

    __slots__ = ("missing_from", "latest_recoverable_time")

    def __init__(self, missing_from, latest_recoverable_time):
        self.missing_from = missing_from
        self.latest_recoverable_time = latest_recoverable_time

    def __repr__(self):
        return f"Gap(missing_from={self.missing_from!r}, " \
               f"latest_recoverable_time={self.latest_recoverable_time!r})"


class Scan:
    """Records read before a gap (or all of them) and the gap, if any."""

    __slots__ = ("records", "gap")

    def __init__(self, records, gap):
        self.records = records
        self.gap = gap

    def __repr__(self):
        return f"Scan(records={len(self.records)}, gap={self.gap!r})"


class _GapFound(Exception):
    def __init__(self, gap):
        super().__init__(gap)
        self.gap = gap


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _malformed(rec):
    """True when a parsed record does not have a usable shape."""
    if not isinstance(rec, dict):
        return True
    if not _is_int(rec.get("lsn")) or not _is_int(rec.get("time")) \
            or not _is_int(rec.get("tx")) or rec.get("type") not in _TYPES:
        return True
    if rec["type"] in ("PUT", "DEL"):
        if not isinstance(rec.get("table"), str) or not isinstance(rec.get("key"), str):
            return True
        if rec["type"] == "PUT" and "value" not in rec:
            return True
    return False


def scan(wal_dir):
    """Read the archive as one stream, stopping at the first gap."""
    names = sorted(n for n in os.listdir(wal_dir) if n.endswith(".jsonl"))
    records = {}
    expected = None
    previous_time = None
    max_declared_end = None
    first_declared_start = None
    gap = None
    try:
        for name in names:
            match = _SEGMENT.match(name)
            declared_start = int(match.group(1)) if match else None
            declared_end = int(match.group(2)) if match else None
            if first_declared_start is None and declared_start is not None:
                first_declared_start = declared_start
            if declared_end is not None and (max_declared_end is None
                                             or declared_end > max_declared_end):
                max_declared_end = declared_end
            try:
                with open(os.path.join(wal_dir, name), encoding="utf-8") as fh:
                    lines = fh.read().split("\n")
            except OSError:
                raise _GapFound(Gap(expected if expected is not None
                                    else (declared_start if declared_start is not None else 1),
                                    previous_time))
            for line in lines:
                if not line.strip():
                    continue
                fallback = expected if expected is not None else (
                    declared_start if declared_start is not None else 1)
                try:
                    rec = json.loads(line)
                except ValueError:
                    raise _GapFound(Gap(fallback, previous_time))
                if _malformed(rec):
                    raise _GapFound(Gap(rec.get("lsn") if isinstance(rec, dict)
                                        and _is_int(rec.get("lsn")) else fallback,
                                        previous_time))
                lsn = rec["lsn"]
                if expected is None:
                    # The stream is anchored at its first present record.  A
                    # lost leading record is only a problem when a restore
                    # actually has to read it; that is checked at replay time.
                    expected = lsn
                if lsn != expected:
                    # A missing record, a duplicate or an out-of-order record:
                    # the stream cannot be trusted from this position on.
                    raise _GapFound(Gap(expected, previous_time))
                if previous_time is not None and rec["time"] <= previous_time:
                    raise _GapFound(Gap(lsn, previous_time))
                records[lsn] = rec
                expected = lsn + 1
                previous_time = rec["time"]
    except _GapFound as exc:
        gap = exc.gap
    if gap is None and records:
        # A truncated final segment still declares how far it should reach.
        last = max(records)
        if max_declared_end is not None and max_declared_end > last:
            gap = Gap(last + 1, records[last]["time"])
    elif gap is None and not records and first_declared_start is not None:
        # Segment files exist but none of them could be read.
        gap = Gap(first_declared_start, None)
    return Scan(records, gap)


def read(wal_dir):
    """Return the trustworthy records before any gap, keyed by lsn."""
    return scan(wal_dir).records
