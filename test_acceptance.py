"""Acceptance tests for the session window engine.

Covers the two acceptance scenarios from the task:
  1. two late events merging three previously published windows, partition
     idle/resume, duplicate and expired events, cross-checked against the
     independent offline grouping, with step-by-step no-orphan retract checks;
  2. failure injected between retract and upsert, checkpoint recovery, two
     deterministic replays, and the session interval invariant after every
     consumer batch.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sessions
from offline_reference import simulate

GAP = 5
LATENESS = 200


def ev(partition, key, event_id, t):
    return {"kind": "event", "partition": partition, "key": key, "id": event_id, "t": t}


def wm(partition, t):
    return {"kind": "watermark", "partition": partition, "t": t}


def idle(partition):
    return {"kind": "idle", "partition": partition}


def resume(partition):
    return {"kind": "resume", "partition": partition}


# Three independently published windows for k1, then two late events that
# chain-merge all three; p2 goes idle and later resumes; a duplicate id and an
# expired event exercise the two dedup destinations.
SOURCE = {
    "partitions": ["p1", "p2"],
    "gap": GAP,
    "lateness": LATENESS,
    "events": [
        ev("p1", "k1", "e1", 1),
        ev("p1", "k1", "e2", 3),          # window A [1, 3]
        ev("p1", "k1", "e3", 12),         # window B [12, 12]
        ev("p1", "k1", "e4", 20),         # window C [20, 20]
        wm("p1", 30),
        wm("p2", 40),                     # global wm = min(30, 40) = 30
        ev("p2", "k2", "f1", 100),
        wm("p2", 40),                     # no backwards move, still 30
        wm("p1", 40),                     # global wm = 40: A, B, C visible
        ev("p1", "k1", "late1", 8),       # merges A + B
        ev("p1", "k1", "late2", 16),      # merges (A,B) + C
        idle("p2"),
        wm("p1", 50),                     # idle p2 excluded, global wm = 50
        resume("p2"),
        wm("p2", 200),                    # global wm = min(50, 200) = 50
        wm("p1", 200),                    # global wm = 200
        ev("p1", "k1", "e3", 12),         # duplicate id: ignored
        ev("p1", "k1", "ex1", -10),       # expired late event -> late list
    ],
}


def row_key(row):
    return row["key"], row["start"], row["end"], tuple(row["event_ids"])


def replay(source, state_path, limit=None, crash=False):
    state = sessions.load_state(state_path) if Path(state_path).exists() else sessions.empty_state()
    engine = sessions.SessionEngine(source, state, crash_during_merge=crash)
    return engine.run(limit, state_path)


class StreamingBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(__file__).parent))
        self.state_path = str(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_final_rows_match_independent_offline_grouping(self):
        state = replay(SOURCE, self.state_path)
        expected = simulate(SOURCE)
        self.assertEqual(state["watermark"], expected["watermark"])
        self.assertEqual(state["rows"], expected["rows"])
        self.assertEqual(state["late"], expected["late"])
        # One merged k1 session and the k2 session.
        self.assertEqual(
            state["rows"],
            [
                {"key": "k1", "start": 1, "end": 20,
                 "event_ids": ["e1", "e2", "e3", "e4", "late1", "late2"]},
                {"key": "k2", "start": 100, "end": 100, "event_ids": ["f1"]},
            ],
        )
        self.assertEqual(state["late"], ["ex1"])

    def test_two_late_events_merge_three_published_windows(self):
        # Prefix up to and including the watermark that publishes A, B, C.
        state = replay(SOURCE, self.state_path, limit=9)
        self.assertEqual(state["watermark"], 40)
        visible = {(r["key"], r["start"], r["end"]) for r in state["rows"]}
        self.assertEqual(
            visible,
            {("k1", 1, 3), ("k1", 12, 12), ("k1", 20, 20)},
        )

        # First late event merges A and B, retracting two published rows.
        state = replay(SOURCE, self.state_path, limit=10)
        last_batch = state["batches"][-1]
        self.assertEqual(last_batch["seq"], 10)
        self.assertEqual(
            [c["op"] for c in last_batch["changes"]],
            ["retract", "retract", "upsert"],
        )
        self.assertEqual(
            last_batch["changes"][-1]["row"]["event_ids"], ["e1", "e2", "e3", "late1"]
        )
        visible = {(r["key"], r["start"], r["end"]) for r in state["rows"]}
        self.assertEqual(visible, {("k1", 1, 12), ("k1", 20, 20)})

        # Second late event chains the merged window with C.
        state = replay(SOURCE, self.state_path, limit=11)
        last_batch = state["batches"][-1]
        self.assertEqual(last_batch["seq"], 11)
        self.assertEqual(
            [c["op"] for c in last_batch["changes"]],
            ["retract", "retract", "upsert"],
        )
        merged = last_batch["changes"][-1]["row"]
        self.assertEqual((merged["key"], merged["start"], merged["end"]), ("k1", 1, 20))
        self.assertEqual(
            merged["event_ids"], ["e1", "e2", "e3", "e4", "late1", "late2"]
        )
        self.assertEqual(len(state["rows"]), 1)

    def test_idle_partition_excluded_and_resume_never_lowers_watermark(self):
        state = replay(SOURCE, self.state_path, limit=12)  # after idle p2
        self.assertTrue(state["partitions"]["p2"]["idle"])
        state = replay(SOURCE, self.state_path, limit=13)  # p1 wm 50
        self.assertEqual(state["watermark"], 50)
        state = replay(SOURCE, self.state_path, limit=14)  # resume p2
        self.assertFalse(state["partitions"]["p2"]["idle"])
        self.assertEqual(state["watermark"], 50)  # p2 wm still 40, not lower
        state = replay(SOURCE, self.state_path, limit=15)  # p2 reports 200
        self.assertEqual(state["watermark"], 50)
        state = replay(SOURCE, self.state_path, limit=16)  # p1 reports 200
        self.assertEqual(state["watermark"], 200)

    def test_duplicate_event_ignored_and_expired_event_goes_to_late(self):
        state = replay(SOURCE, self.state_path, limit=17)
        rows_before = json.loads(json.dumps(state["rows"]))
        batches_before = len(state["batches"])
        state = replay(SOURCE, self.state_path, limit=18)
        self.assertEqual(state["rows"], rows_before)
        self.assertEqual(len(state["batches"]), batches_before)
        self.assertEqual(state["late"], ["ex1"])
        # Duplicate id must not be double-counted inside any session.
        k1 = next(s for s in state["sessions"]["k1"])
        self.assertEqual(sorted(k1["event_ids"]).count("e3"), 1)

    def test_no_orphan_retracts_and_batch_ordering_at_every_step(self):
        state_path = self.state_path
        for limit in range(1, len(SOURCE["events"]) + 1):
            state = replay(SOURCE, state_path, limit=limit)
            # Rebuild the view by applying every batch from scratch in order.
            view = {}
            for batch in state["batches"]:
                ops = [c["op"] for c in batch["changes"]]
                # All retracts precede all upserts.
                self.assertLessEqual(
                    max((i for i, op in enumerate(ops) if op == "retract"), default=-1),
                    min((i for i, op in enumerate(ops) if op == "upsert"), default=len(ops)),
                )
                retract_rows = [c["row"] for c in batch["changes"] if c["op"] == "retract"]
                upsert_rows = [c["row"] for c in batch["changes"] if c["op"] == "upsert"]
                self.assertEqual(retract_rows, sorted(retract_rows, key=row_key))
                self.assertEqual(upsert_rows, sorted(upsert_rows, key=row_key))
                for row in retract_rows:
                    # No orphan retract: the row is currently held downstream.
                    self.assertIn(row_key(row), view, f"orphan retract at seq {batch['seq']}")
                    del view[row_key(row)]
                for row in upsert_rows:
                    view[row_key(row)] = row
            # The rebuilt view must equal the checkpointed visible rows.
            self.assertEqual(sorted(view.keys()), [row_key(r) for r in state["rows"]])
            # Every batch carries its trigger and state source.
            for batch in state["batches"]:
                self.assertIn("source", batch)
                self.assertEqual(batch["source"]["seq"], batch["seq"])
                self.assertIn("kind", batch["source"])

    def test_session_interval_invariant_after_every_batch(self):
        timestamps = {}
        for event in SOURCE["events"]:
            if event["kind"] == "event":
                timestamps[event["id"]] = event["t"]
        state = replay(SOURCE, self.state_path)
        view = {}
        for batch in state["batches"]:
            for change in batch["changes"]:
                key = row_key(change["row"])
                if change["op"] == "retract":
                    del view[key]
                else:
                    view[key] = change["row"]
            # After each consumer batch: adjacent events in each visible row
            # must be within the session gap.
            for row in view.values():
                ordered = sorted(timestamps[i] for i in row["event_ids"])
                for previous, current in zip(ordered, ordered[1:]):
                    self.assertLessEqual(current - previous, GAP)

    def test_limit_prefix_is_idempotent(self):
        first = replay(SOURCE, self.state_path, limit=11)
        batches = len(first["batches"])
        again = replay(SOURCE, self.state_path, limit=11)
        self.assertEqual(len(again["batches"]), batches)
        self.assertEqual(again["processed"], 11)
        # Continuing to the full source reaches the same final state.
        full = replay(SOURCE, self.state_path)
        self.assertEqual(full["rows"], simulate(SOURCE)["rows"])


class CrashRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(__file__).parent))
        self.dirpath = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cli(self, source_path, state_path, crash=False):
        cmd = [sys.executable, "sessions.py", "apply", source_path, state_path]
        if crash:
            cmd.append("--crash-during-merge")
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent))

    def test_crash_between_retract_and_upsert_then_recovery(self):
        source_path = self.dirpath / "source.json"
        source_path.write_text(json.dumps(SOURCE))
        crashed_state = self.dirpath / "crashed.json"
        clean_state = self.dirpath / "clean.json"

        proc = self._run_cli(str(source_path), str(crashed_state), crash=True)
        self.assertEqual(proc.returncode, sessions.CRASH_EXIT_CODE, proc.stderr)
        staging = Path(str(crashed_state) + sessions.STAGING_SUFFIX)
        self.assertTrue(staging.exists())
        staged = json.loads(staging.read_text())
        self.assertTrue(all(c["op"] == "retract" for c in staged["staged"]))
        self.assertGreaterEqual(len(staged["staged"]), 2)

        # Checkpoint is still the pre-merge state; the batch never became visible.
        checkpoint = sessions.load_state(crashed_state)
        self.assertEqual(checkpoint["processed"], staged["seq"] - 1)
        self.assertEqual(len(checkpoint["rows"]), 3)

        # Recovery re-runs the event under its original seq and drops staging.
        proc = self._run_cli(str(source_path), str(crashed_state))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(staging.exists())
        recovered = sessions.load_state(crashed_state)

        clean = self._run_cli(str(source_path), str(clean_state))
        self.assertEqual(clean.returncode, 0, clean.stderr)
        clean_final = sessions.load_state(clean_state)
        self.assertEqual(recovered, clean_final)
        # The last change batch is the final watermark (seq 16); the trailing
        # duplicate (17) and expired (18) events checkpoint progress only.
        self.assertEqual(recovered["batches"][-1]["seq"], 16)
        self.assertEqual(recovered["processed"], len(SOURCE["events"]))
        merge_batch = next(b for b in recovered["batches"] if b["seq"] == 10)
        self.assertEqual(
            [c["op"] for c in merge_batch["changes"]],
            ["retract", "retract", "upsert"],
        )

    def test_two_replays_produce_identical_materialized_tables(self):
        paths = [self.dirpath / f"run{i}.json" for i in range(2)]
        source_path = self.dirpath / "source.json"
        source_path.write_text(json.dumps(SOURCE))
        for path in paths:
            proc = self._run_cli(str(source_path), str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
        states = [sessions.load_state(path) for path in paths]
        self.assertEqual(states[0]["rows"], states[1]["rows"])
        self.assertEqual(states[0]["batches"], states[1]["batches"])
        self.assertEqual(states[0], states[1])
        # Re-applying the same full input creates no new batches.
        before = len(states[1]["batches"])
        proc = self._run_cli(str(source_path), str(paths[1]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(sessions.load_state(paths[1])["batches"]), before)


class BoundarySemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(__file__).parent))
        self.state_path = str(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lateness_boundary_equality_is_accepted(self):
        source = {
            "partitions": ["p"],
            "gap": 5,
            "lateness": 10,
            "events": [
                ev("p", "k", "a", 100),
                wm("p", 120),                      # wm = 120, session visible
                ev("p", "k", "boundary", 105),     # 105 + 5 + 10 == 120: accepted
                ev("p", "k", "expired", 104),      # 104 + 5 + 10 == 119 < 120: late
            ],
        }
        state = replay(source, self.state_path)
        self.assertIn("boundary", state["seen"])
        self.assertNotIn("boundary", state["late"])
        self.assertEqual(state["late"], ["expired"])
        self.assertEqual(state["rows"][0]["event_ids"], ["a", "boundary"])
        # Extending one published session still retracts the old row first.
        batch = state["batches"][-1]
        self.assertEqual([c["op"] for c in batch["changes"]], ["retract", "upsert"])

    def test_partition_and_global_watermarks_never_move_backwards(self):
        source = {
            "partitions": ["p", "q"],
            "gap": 5,
            "lateness": 10,
            "events": [
                wm("p", 50),
                wm("q", 60),     # global = 50
                wm("p", 40),     # stale report ignored
                wm("q", 30),     # stale report ignored
            ],
        }
        state = replay(source, self.state_path)
        self.assertEqual(state["partitions"]["p"]["watermark"], 50)
        self.assertEqual(state["partitions"]["q"]["watermark"], 60)
        self.assertEqual(state["watermark"], 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
