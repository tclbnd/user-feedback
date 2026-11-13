"""Streaming session window replay with late-event merging and retractions.

State model (one JSON document, atomically replaced on every committed batch):

  processed            number of source events already consumed (checkpoint)
  watermark            global watermark across active partitions
  partitions           per-partition watermark / idle status
  seen                 deduplicated event ids (accepted and expired alike)
  sessions             per-key session intervals, each with event ids
  rows                 currently visible rows (downstream materialized view)
  batches              ordered, immutable output batches (retracts then upserts)
  late                 expired late events, kept in arrival order (explicit sink)
  last_seq / last_batch_seq  output positions

A session is visible when its last event time + gap <= global watermark.
A late event whose t + gap + lateness < global watermark is rejected to the
``late`` list (equality is still accepted).  Accepted late events may reopen
and chain-merge already published sessions; every such change is emitted as
one atomic batch with all retracts before all upserts.
"""
import argparse
import json
import os
import sys
from pathlib import Path

CRASH_EXIT_CODE = 97
STAGING_SUFFIX = ".staging"


def empty_state():
    return {
        "processed": 0,
        "watermark": None,
        "partitions": {},
        "seen": [],
        "sessions": {},
        "rows": [],
        "batches": [],
        "late": [],
        "last_seq": 0,
        "last_batch_seq": 0,
    }


def load_state(path):
    path = Path(path)
    if path.exists():
        state = json.loads(path.read_text())
    else:
        state = empty_state()
    # Forward-compatible checkpoint restore: fill in any missing field.
    defaults = empty_state()
    for key, value in defaults.items():
        if key not in state:
            state[key] = type(value)()
    return state


# --------------------------------------------------------------------------- #
# Pure session mechanics
# --------------------------------------------------------------------------- #

def row_tuple(row):
    return row["key"], row["start"], row["end"], tuple(row["event_ids"])


def is_visible(session, watermark, gap):
    return watermark is not None and session["end"] + gap <= watermark


def diff_changes(before_rows, after_rows):
    """Retract full rows that disappeared, upsert rows that appeared/changed.

    Rows are immutable value tuples (key, start, end, ids); every retract
    references a row the downstream view currently holds, and changes follow
    the required ordering convention (retracts first, then upserts; each group
    sorted by key, start, end).
    """
    before = {row_tuple(r) for r in before_rows}
    after = {row_tuple(r) for r in after_rows}
    retracts = sorted(before - after)
    upserts = sorted(after - before)
    changes = [{"op": "retract", "row": _row_from_tuple(r)} for r in retracts]
    changes.extend({"op": "upsert", "row": _row_from_tuple(r)} for r in upserts)
    return changes


def _row_from_tuple(row):
    key, start, end, event_ids = row
    return {"key": key, "start": start, "end": end, "event_ids": list(event_ids)}


def sort_rows(rows):
    return sorted(rows, key=row_tuple)


def visible_rows(state, gap):
    rows = []
    for key, sessions in state["sessions"].items():
        for session in sessions:
            if is_visible(session, state["watermark"], gap):
                rows.append({
                    "key": key,
                    "start": session["start"],
                    "end": session["end"],
                    "event_ids": sorted(session["event_ids"]),
                })
    return sort_rows(rows)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class SessionEngine:
    def __init__(self, source, state, crash_during_merge=False):
        self.gap = source["gap"]
        self.lateness = source["lateness"]
        self.partition_names = list(source.get("partitions", []))
        self.events = source["events"]
        self.state = state
        self.crash_during_merge = crash_during_merge
        self.staging_path = None
        self._state_path = None

    # -- watermarks --------------------------------------------------------- #

    def recompute_global_watermark(self):
        """Minimum watermark over active partitions; never moves backwards.

        Idle partitions do not participate.  Active partitions without a
        reported watermark hold the global value.  When every partition is
        idle (or none has reported yet) the previous value is retained.
        """
        active_values = [
            p["watermark"]
            for p in self.state["partitions"].values()
            if not p["idle"] and p["watermark"] is not None
        ]
        if active_values:
            candidate = min(active_values)
            if self.state["watermark"] is None or candidate > self.state["watermark"]:
                self.state["watermark"] = candidate

    def _partition(self, name):
        partition = self.state["partitions"].get(name)
        if partition is None:
            partition = {"watermark": None, "idle": False}
            self.state["partitions"][name] = partition
        return partition

    # -- event insertion ---------------------------------------------------- #

    def _insert(self, key, event_id, t):
        """Insert one accepted event, chain-merging every adjacent session.

        The merge loop repeats until no adjacency remains, so one event (for
        example a late one) can bridge any number of previously independent,
        possibly already published sessions.
        """
        sessions = self.state["sessions"].setdefault(key, [])
        target = {"key": key, "start": t, "end": t, "event_ids": [event_id]}
        sessions.append(target)
        while True:
            sessions.sort(key=lambda s: s["start"])
            out = []
            merged_any = False
            for session in sessions:
                if not out:
                    out.append(session)
                    continue
                previous = out[-1]
                if session["start"] - previous["end"] <= self.gap:
                    previous["start"] = min(previous["start"], session["start"])
                    previous["end"] = max(previous["end"], session["end"])
                    previous["event_ids"] = sorted(
                        set(previous["event_ids"]) | set(session["event_ids"])
                    )
                    target = previous
                    merged_any = True
                else:
                    out.append(session)
            sessions[:] = out
            if not merged_any:
                return target

    # -- batching / checkpoints --------------------------------------------- #

    def _commit(self, seq, trigger, changes):
        """Append one atomic output batch and checkpoint the whole state.

        Crash injection for --crash-during-merge happens here: the retract
        portion is staged on disk, then the process exits before the batch and
        its progress become visible.  The main checkpoint stays untouched, so
        recovery re-runs the event under its original 1-based seq.
        """
        retract_count = sum(1 for c in changes if c["op"] == "retract")
        upsert_count = sum(1 for c in changes if c["op"] == "upsert")
        if self.crash_during_merge and retract_count >= 2 and upsert_count >= 1:
            staging = {
                "seq": seq,
                "trigger": trigger,
                "staged": [c for c in changes if c["op"] == "retract"],
            }
            self.staging_path = str(self._state_path) + STAGING_SUFFIX
            Path(self.staging_path).write_text(
                json.dumps(staging, sort_keys=True) + "\n"
            )
            sys.stderr.write(
                "crash injection: staged %d retract(s) at seq %d "
                "before the batch became visible\n" % (retract_count, seq)
            )
            sys.exit(CRASH_EXIT_CODE)

        self.state["last_seq"] = seq
        batch_seq = self.state["last_batch_seq"] + 1
        batch = {
            "batch": batch_seq,
            "seq": seq,
            "source": trigger,
            "watermark": self.state["watermark"],
            "changes": changes,
        }
        self.state["batches"].append(batch)
        self.state["last_batch_seq"] = batch_seq
        self.state["rows"] = visible_rows(self.state, self.gap)
        self._flush()

    def _flush(self):
        tmp = str(self._state_path) + ".tmp"
        Path(tmp).write_text(json.dumps(self.state, sort_keys=True) + "\n")
        os.replace(tmp, self._state_path)

    def _flush_progress(self, trigger):
        """Checkpoint progress (watermark/intervals/output position) even when
        the visible result is unchanged."""
        self.state["last_seq"] = trigger["seq"]
        self.state["rows"] = visible_rows(self.state, self.gap)
        self._flush()

    # -- main driver -------------------------------------------------------- #

    def run(self, limit, state_path):
        self._state_path = Path(state_path)
        start_index = self.state["processed"]
        if limit is not None and limit <= start_index:
            # Re-reading an already consumed prefix produces no new batches.
            return self.state
        upper = len(self.events) if limit is None else min(limit, len(self.events))

        for index in range(start_index, upper):
            event = self.events[index]
            seq = index + 1  # 1-based source event number
            self.state["processed"] = seq
            kind = event["kind"]
            trigger = {"seq": seq, "kind": kind}

            if kind == "watermark":
                partition = self._partition(event["partition"])
                if partition["watermark"] is None or event["t"] > partition["watermark"]:
                    partition["watermark"] = event["t"]
                trigger.update(partition=event["partition"], t=event["t"])
                self.recompute_global_watermark()

            elif kind == "idle":
                partition = self._partition(event["partition"])
                partition["idle"] = True
                trigger.update(partition=event["partition"])
                self.recompute_global_watermark()

            elif kind == "resume":
                partition = self._partition(event["partition"])
                partition["idle"] = False
                trigger.update(partition=event["partition"])
                # Reactivation never lowers the global watermark.
                self.recompute_global_watermark()

            elif kind == "event":
                trigger.update(
                    partition=event["partition"],
                    key=event["key"],
                    id=event["id"],
                    t=event["t"],
                )
                self._partition(event["partition"])
                event_id = event["id"]
                if event_id in self.state["seen"]:
                    # Duplicate source ids are ignored deterministically; the
                    # trigger is still recorded via the checkpoint position.
                    trigger["dedup"] = "duplicate-ignored"
                    self._flush_progress(trigger)
                    continue

                if (
                    self.state["watermark"] is not None
                    and event["t"] + self.gap + self.lateness < self.state["watermark"]
                ):
                    # Explicitly defined destination for expired late events.
                    self.state["seen"].append(event_id)
                    self.state["late"].append(event_id)
                    trigger["dedup"] = "late-expired"
                    self._flush_progress(trigger)
                    continue

                self.state["seen"].append(event_id)
                before = [dict(r) for r in self.state["rows"]]
                self._insert(event["key"], event_id, event["t"])
                after = visible_rows(self.state, self.gap)
                changes = diff_changes(before, after)
                if changes:
                    self._commit(seq, trigger, changes)
                else:
                    self._flush_progress(trigger)
                continue
            else:
                trigger["dedup"] = "unknown-kind"
                self._flush_progress(trigger)
                continue

            # Watermark / idle / resume: visibility may change globally.
            before = [dict(r) for r in self.state["rows"]]
            after = visible_rows(self.state, self.gap)
            changes = diff_changes(before, after)
            if changes:
                self._commit(seq, trigger, changes)
            else:
                self._flush_progress(trigger)

        return self.state


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def apply(source, state_path, limit=None, crash=False):
    state_path = Path(state_path)
    staging = Path(str(state_path) + STAGING_SUFFIX)
    if staging.exists():
        # A previous crash left staged retracts that never became visible.
        # Recovery discards staging and re-runs the event from the intact
        # checkpoint under its original seq.
        staging.unlink()
    state = load_state(state_path)
    engine = SessionEngine(source, state, crash_during_merge=crash)
    return engine.run(limit, state_path)


def inspect(state_path):
    return load_state(Path(state_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply", help="replay source events into a state file")
    apply_parser.add_argument("source", type=Path)
    apply_parser.add_argument("state", type=Path)
    apply_parser.add_argument("--limit", type=int)
    apply_parser.add_argument("--crash-during-merge", action="store_true")
    inspect_parser = sub.add_parser("inspect", help="print the checkpointed state")
    inspect_parser.add_argument("state", type=Path)
    args = parser.parse_args(argv)

    if args.command == "apply":
        source = json.loads(args.source.read_text())
        result = apply(source, args.state, args.limit, args.crash_during_merge)
    else:
        result = inspect(args.state)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
