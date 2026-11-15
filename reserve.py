"""Command line entry point for the offline backfill reservation tool.

Usage::

    python3 reserve.py input.json output.json --journal journal.jsonl

The journal is the source of truth.  Accepted events are appended to it and
made durable *before* the result document is written, so a crash at any point
is recovered by replaying the journal.  Only local files and the standard
library are used.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from reservations import process

EXIT_INVALID = 2
EXIT_CRASH_SIMULATED = 75

VALID_OPS = ("reserve", "publish", "release", "tick")


class InvalidBatch(Exception):
    """A batch, journal or input file that must be rejected with exit code 2."""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_event(event):
    """Return a canonical error string, or None if the event is well formed."""
    if not isinstance(event, dict):
        return "event is not an object"
    seq = event.get("seq")
    if not _is_int(seq) or seq < 1:
        return "seq must be a positive integer"
    at = event.get("at")
    if not _is_int(at) or at < 0:
        return "at must be a non-negative integer"
    op = event.get("op")
    if op not in VALID_OPS:
        return "unknown op"
    if op == "tick":
        return None
    key = event.get("key")
    if not isinstance(key, str) or not key:
        return "key must be a non-empty string"
    if op == "reserve":
        start = event.get("start")
        end = event.get("end")
        ttl = event.get("ttl")
        if not _is_int(start) or not _is_int(end):
            return "start and end must be integers"
        if not start < end:
            return "reserve requires start < end"
        if not _is_int(ttl) or ttl < 1:
            return "ttl must be a positive integer"
        return None
    # publish / release
    lease = event.get("lease")
    if not _is_int(lease) or lease < 1:
        return "lease must be a positive integer"
    epoch = event.get("epoch")
    if not _is_int(epoch) or epoch < 1:
        return "epoch must be a positive integer"
    return None


def _canonical(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _read_journal(path):
    """Read the committed journal records.

    Returns ``(events, committed_bytes)``.  ``committed_bytes`` is the length of
    the durable prefix; a trailing fragment without a newline is treated as an
    uncommitted partial record and ignored (and later truncated before append).
    A damaged record anywhere in the committed prefix raises ``InvalidBatch``.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], 0
    except OSError as exc:
        raise InvalidBatch("cannot read journal: %s" % exc)

    end = raw.rfind(b"\n")
    committed_bytes = end + 1
    events = []
    last_at = -1
    expected = 1
    if end >= 0:
        for line in raw[:committed_bytes].split(b"\n")[:-1]:
            if not line.strip():
                raise InvalidBatch("journal contains a blank line")
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InvalidBatch("journal record is not valid JSON: %s" % exc)
            problem = _validate_event(event)
            if problem is not None:
                raise InvalidBatch("journal record is invalid: %s" % problem)
            seq = event["seq"]
            if seq != expected:
                raise InvalidBatch(
                    "journal is not contiguous: expected seq %d, found %d"
                    % (expected, seq)
                )
            if event["at"] < last_at:
                raise InvalidBatch("journal logical time goes backwards")
            expected += 1
            last_at = event["at"]
            events.append(event)
    return events, committed_bytes


def _read_input(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidBatch("cannot read input: %s" % exc)
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise InvalidBatch('input must be an object with an "events" list')
    events = document["events"]
    for event in events:
        problem = _validate_event(event)
        if problem is not None:
            raise InvalidBatch("invalid event: %s" % problem)
    return events


def _plan(history, incoming):
    """Merge the incoming batch with the committed history.

    Returns the list of genuinely new events in seq order.  Duplicate seqs with
    identical content are accepted and reuse the original result; everything
    else that breaks ordering or continuity rejects the whole batch.
    """
    committed = {}
    last_at = -1
    for event in history:
        committed[event["seq"]] = _canonical(event)
        last_at = event["at"]

    merged = {}
    for event in incoming:
        seq = event["seq"]
        canonical = _canonical(event)
        if seq in committed:
            if committed[seq] != canonical:
                raise InvalidBatch(
                    "seq %d was already committed with different content" % seq
                )
            continue
        if seq in merged:
            if merged[seq][0] != canonical:
                raise InvalidBatch("seq %d appears with conflicting content" % seq)
            continue
        merged[seq] = (canonical, event)

    if not merged:
        return []

    new_events = [merged[seq][1] for seq in sorted(merged)]
    expected = len(history) + 1
    previous_at = last_at
    for event in new_events:
        seq = event["seq"]
        if seq != expected:
            # Duplicates were folded above, so any mismatch is a gap.
            raise InvalidBatch("seq gap: expected %d, found %d" % (expected, seq))
        if event["at"] < previous_at:
            raise InvalidBatch("logical time goes backwards at seq %d" % seq)
        previous_at = event["at"]
        expected += 1
    return new_events


def _atomic_write_json(path, document):
    directory = path.parent if str(path.parent) else Path(".")
    payload = json.dumps(document, sort_keys=True) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _append_events(path, events, committed_bytes, cut_after):
    """Durably append events; optionally stop right after seq ``cut_after``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        # Drop an uncommitted trailing fragment from a previous crash.
        stream.truncate(committed_bytes)
        stream.seek(committed_bytes)
        for event in events:
            stream.write((json.dumps(event) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
            if cut_after is not None and event["seq"] == cut_after:
                raise SimulatedCrash()


class SimulatedCrash(Exception):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--cut-after-journal", type=int, default=None, metavar="N")
    args = parser.parse_args(argv)

    try:
        history, committed_bytes = _read_journal(args.journal)
        incoming = _read_input(args.input)
        new_events = _plan(history, incoming)
    except InvalidBatch as exc:
        print("reserve: %s" % exc, file=sys.stderr)
        return EXIT_INVALID

    combined = list(history) + list(new_events)
    outcome = process(combined)

    try:
        # A crash point at or below the durable prefix means the scheduler
        # wants to stop right after that record: nothing new may be appended
        # and the output must not be written.
        if args.cut_after_journal is not None and args.cut_after_journal <= len(history):
            raise SimulatedCrash()
        _append_events(args.journal, new_events, committed_bytes, args.cut_after_journal)
    except SimulatedCrash:
        print(
            "reserve: simulated crash after journaling seq %d" % args.cut_after_journal,
            file=sys.stderr,
        )
        return EXIT_CRASH_SIMULATED
    except OSError as exc:
        print("reserve: cannot write journal: %s" % exc, file=sys.stderr)
        return EXIT_INVALID

    try:
        _atomic_write_json(args.output, outcome)
    except OSError as exc:
        print("reserve: cannot write output: %s" % exc, file=sys.stderr)
        return EXIT_INVALID
    return 0


if __name__ == "__main__":
    sys.exit(main())

