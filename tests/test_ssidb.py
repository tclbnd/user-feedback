import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import Engine, SerializationFailure  # noqa: E402

FAILURE = {"error": "serialization_failure", "code": "40001", "retryable": True}


def run_schedule(schedule):
    """Replay a schedule dict and return (per-op results, engine outcome)."""
    engine = Engine(schedule.get("initial"))
    results = []
    for op in schedule["ops"]:
        kind = op["op"]
        if kind == "begin":
            engine.begin(op["tx"], op.get("read_only", False))
            results.append({"ok": True})
            continue
        tx = engine.txns[op["tx"]]
        if tx.status != "running":
            results.append({"error": tx.status})
            continue
        try:
            if kind == "get":
                results.append({"value": engine.read(tx, op["key"])})
            elif kind == "scan":
                results.append({"rows": engine.read(tx, op["lo"], op["hi"])})
            elif kind in ("put", "delete"):
                if tx.read_only:
                    results.append({"error": "read_only"})
                    continue
                engine.write(tx, op["key"], op.get("value"))
                results.append({"ok": True})
            elif kind == "commit":
                engine.commit(tx)
                results.append({"ok": True})
            elif kind == "abort":
                engine.abort(tx)
                results.append({"ok": True})
            else:
                raise AssertionError(f"unknown op {kind}")
        except SerializationFailure:
            results.append(dict(FAILURE))
    return results, engine.outcome()


def serial_state(schedule, order):
    """Final state when the committed txns run one after another in `order`."""
    state = {int(k): v for k, v in (schedule.get("initial") or {}).items()}
    for name in order:
        for op in schedule["ops"]:
            if op["tx"] != name or op["op"] not in ("put", "delete"):
                continue
            if op["op"] == "put":
                state[op["key"]] = op["value"]
            else:
                state.pop(op["key"], None)
    return {str(k): v for k, v in sorted(state.items()) if v is not None}


def assert_serializable(testcase, schedule, outcome):
    """At least one serial order of the committed txns yields the state."""
    committed = outcome["committed"]
    final_state = outcome["state"]
    matches = [order for order in itertools.permutations(committed)
               if serial_state(schedule, list(order)) == final_state]
    testcase.assertTrue(
        matches,
        f"state {final_state} matches no serial order of {committed}")


class EngineTests(unittest.TestCase):
    def test_snapshot_read(self):
        e = Engine({"1": "a"})
        r, w = e.begin("R"), e.begin("W")
        e.write(w, 1, "b")
        e.commit(w)
        self.assertEqual(e.read(r, 1), "a")
        self.assertEqual(e.read(e.begin("N"), 1), "b")

    def test_first_updater_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            ops = [{"op": "begin", "tx": "A"}, {"op": "begin", "tx": "B"},
                   {"op": "put", "tx": "A", "key": 1, "value": 1}, {"op": "put", "tx": "B", "key": 1, "value": 2},
                   {"op": "commit", "tx": "A"}]
            with open(path, "w") as fh:
                json.dump({"ops": ops}, fh)
            out = json.loads(subprocess.run([sys.executable, os.path.join(ROOT, "ssidb.py"), "run", path],
                                            capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["results"][3]["code"], "40001")
        self.assertEqual(out["committed"], ["A"])

    # -- 1. classic write skew: two on-call takedowns ---------------------
    def test_write_skew_oncall_roster(self):
        schedule = {
            "initial": {"1": "alice", "2": "bob"},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "begin", "tx": "B"},
                {"op": "scan", "tx": "A", "lo": 1, "hi": 9},
                {"op": "scan", "tx": "B", "lo": 1, "hi": 9},
                {"op": "delete", "tx": "A", "key": 1},
                {"op": "commit", "tx": "A"},
                {"op": "delete", "tx": "B", "key": 2},
                {"op": "commit", "tx": "B"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["A"])
        self.assertEqual(outcome["aborted"], ["B"])
        # The second takedown is a retryable 40001, then ops see "aborted".
        self.assertEqual(results[6], FAILURE)
        self.assertEqual(results[7], {"error": "aborted"})
        # At least one person remains on call; outcome is serial-equivalent.
        self.assertEqual(outcome["state"], {"2": "bob"})
        assert_serializable(self, schedule, outcome)

    # -- 2. read-only anomaly: safe snapshots are never killed ------------
    def test_read_only_safe_snapshot_not_aborted(self):
        schedule = {
            "initial": {"10": 0, "20": 0},
            "ops": [
                {"op": "begin", "tx": "P"},
                {"op": "get", "tx": "P", "key": 10},
                {"op": "get", "tx": "P", "key": 20},
                {"op": "begin", "tx": "Q"},
                {"op": "put", "tx": "Q", "key": 20, "value": 20},
                {"op": "commit", "tx": "Q"},
                {"op": "begin", "tx": "R", "read_only": True},
                {"op": "get", "tx": "R", "key": 10},
                {"op": "get", "tx": "R", "key": 20},
                {"op": "commit", "tx": "R"},
                {"op": "put", "tx": "P", "key": 10, "value": -11},
                {"op": "commit", "tx": "P"},
                {"op": "begin", "tx": "S", "read_only": True},
                {"op": "begin", "tx": "U"},
                {"op": "get", "tx": "U", "key": 10},
                {"op": "get", "tx": "U", "key": 20},
                {"op": "get", "tx": "S", "key": 10},
                {"op": "begin", "tx": "V"},
                {"op": "put", "tx": "V", "key": 20, "value": 40},
                {"op": "commit", "tx": "V"},
                {"op": "get", "tx": "S", "key": 20},
                {"op": "commit", "tx": "S"},
                {"op": "put", "tx": "U", "key": 10, "value": -50},
                {"op": "commit", "tx": "U"},
            ]}
        results, outcome = run_schedule(schedule)
        # Only the read-write pivot P is cancelled. The read-only R and S
        # hold safe snapshots and must survive.
        self.assertEqual(outcome["committed"], ["Q", "R", "V", "S", "U"])
        self.assertEqual(outcome["aborted"], ["P"])
        self.assertTrue(all(r != FAILURE for r in
                            [results[i] for i in (7, 8, 9, 16, 20, 21)]))
        assert_serializable(self, schedule, outcome)

    def test_unsafe_read_only_t1_is_fallback_victim(self):
        # Read-only R holds the T1 slot of T1 -> T2 -> T3. T3 commits
        # before R began, so R has no safe snapshot. Pivot T2 commits
        # first; R only reads T2's now-hidden key afterwards, recording
        # R -> T2 once T2 is already committed. The fallback victim is
        # therefore the read-only R: read-only txns are protected only
        # while their snapshot is safe.
        schedule = {
            "initial": {"1": 0, "2": 0, "3": 0},
            "ops": [
                {"op": "begin", "tx": "T2"},
                {"op": "get", "tx": "T2", "key": 2},
                {"op": "begin", "tx": "T3"},
                {"op": "get", "tx": "T3", "key": 3},
                {"op": "put", "tx": "T3", "key": 2, "value": 3},
                {"op": "commit", "tx": "T3"},
                {"op": "begin", "tx": "R", "read_only": True},
                {"op": "put", "tx": "T2", "key": 1, "value": 2},
                {"op": "commit", "tx": "T2"},
                {"op": "get", "tx": "R", "key": 1},
                {"op": "commit", "tx": "R"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["T3", "T2"])
        self.assertEqual(outcome["aborted"], ["R"])
        self.assertEqual(results[9], FAILURE)
        self.assertEqual(results[10], {"error": "aborted"})
        assert_serializable(self, schedule, outcome)

    def test_victim_priority_prefers_live_pivot_over_read_only_t1(self):
        # Same dangerous triple R -> T2 -> T3 with an unsafe read-only T1
        # (R began after T3 committed), but this time pivot T2 has not
        # committed when the structure closes. Victim priority cancels
        # the live pivot T2, not the read-only T1.
        schedule = {
            "initial": {"1": 0, "2": 0, "3": 0},
            "ops": [
                {"op": "begin", "tx": "T2"},
                {"op": "get", "tx": "T2", "key": 2},
                {"op": "begin", "tx": "T3"},
                {"op": "get", "tx": "T3", "key": 3},
                {"op": "put", "tx": "T3", "key": 2, "value": 3},
                {"op": "commit", "tx": "T3"},
                {"op": "begin", "tx": "R", "read_only": True},
                {"op": "get", "tx": "R", "key": 1},
                {"op": "put", "tx": "T2", "key": 1, "value": 2},
                {"op": "commit", "tx": "T2"},
                {"op": "commit", "tx": "R"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["T3", "R"])
        self.assertEqual(outcome["aborted"], ["T2"])
        self.assertEqual(results[8], FAILURE)
        assert_serializable(self, schedule, outcome)

    # -- 3. three-transaction rw-antidependency ring ----------------------
    def test_three_transaction_ring(self):
        path = os.path.join(ROOT, "examples", "ring.json")
        with open(path, encoding="utf-8") as fh:
            schedule = json.load(fh)
        results, outcome = run_schedule(schedule)
        # C commits first; the pivot A (incoming C->A, outgoing A->B) is
        # cancelled, B survives. Two txns commit, one is aborted.
        self.assertEqual(outcome["committed"], ["C", "B"])
        self.assertEqual(outcome["aborted"], ["A"])
        self.assertEqual(results[10], FAILURE)
        assert_serializable(self, schedule, outcome)

    # -- 4. range predicate conflicts (phantoms) --------------------------
    def test_range_predicate_conflict(self):
        schedule = {
            "initial": {"50": "standup"},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "begin", "tx": "B"},
                {"op": "scan", "tx": "A", "lo": 100, "hi": 199},
                {"op": "scan", "tx": "B", "lo": 100, "hi": 199},
                {"op": "put", "tx": "A", "key": 150, "value": "review"},
                {"op": "put", "tx": "B", "key": 160, "value": "retro"},
                {"op": "commit", "tx": "A"},
                {"op": "commit", "tx": "B"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["A"])
        self.assertEqual(outcome["aborted"], ["B"])
        # Neither 150 nor 160 existed at scan time; the range registration
        # still catches the phantom insert.
        self.assertEqual(outcome["state"], {"50": "standup", "150": "review"})
        assert_serializable(self, schedule, outcome)

    def test_get_on_missing_key_registers_predicate(self):
        # A point read of an absent key is registered too: a concurrent
        # insert of that key creates an antidependency.
        schedule = {
            "initial": {"7": "x"},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "begin", "tx": "B"},
                {"op": "get", "tx": "A", "key": 8},
                {"op": "get", "tx": "B", "key": 8},
                {"op": "put", "tx": "A", "key": 8, "value": "a"},
                {"op": "commit", "tx": "A"},
                {"op": "put", "tx": "B", "key": 8, "value": "b"},
                {"op": "commit", "tx": "B"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["A"])
        self.assertEqual(outcome["aborted"], ["B"])
        assert_serializable(self, schedule, outcome)

    # -- conflict-free and one-way-conflict workloads never abort ---------
    def test_disjoint_data_never_aborted(self):
        schedule = {
            "initial": {"1": "a", "2": "b"},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "begin", "tx": "B"},
                {"op": "get", "tx": "A", "key": 1},
                {"op": "get", "tx": "B", "key": 2},
                {"op": "put", "tx": "A", "key": 1, "value": "a2"},
                {"op": "put", "tx": "B", "key": 2, "value": "b2"},
                {"op": "commit", "tx": "A"},
                {"op": "commit", "tx": "B"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["A", "B"])
        self.assertEqual(outcome["aborted"], [])
        self.assertEqual(outcome["state"], {"1": "a2", "2": "b2"})
        self.assertFalse(any("error" in r for r in results))

    def test_one_way_edges_never_aborted(self):
        # All edges point A -> B; no pivot exists, so nothing is cancelled.
        schedule = {
            "initial": {"1": 0, "2": 0},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "get", "tx": "A", "key": 1},
                {"op": "get", "tx": "A", "key": 2},
                {"op": "begin", "tx": "B"},
                {"op": "put", "tx": "B", "key": 1, "value": 1},
                {"op": "put", "tx": "B", "key": 2, "value": 2},
                {"op": "commit", "tx": "B"},
                {"op": "commit", "tx": "A"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["B", "A"])
        self.assertEqual(outcome["aborted"], [])
        assert_serializable(self, schedule, outcome)

    def test_read_only_writes_rejected_but_txn_lives(self):
        # Writes in a read-only transaction return read_only without
        # disturbing the transaction or the serializable machinery.
        schedule = {
            "initial": {"1": "a"},
            "ops": [
                {"op": "begin", "tx": "R", "read_only": True},
                {"op": "put", "tx": "R", "key": 1, "value": "b"},
                {"op": "delete", "tx": "R", "key": 1},
                {"op": "get", "tx": "R", "key": 1},
                {"op": "commit", "tx": "R"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(results[1], {"error": "read_only"})
        self.assertEqual(results[2], {"error": "read_only"})
        self.assertEqual(results[3], {"value": "a"})
        self.assertEqual(outcome["committed"], ["R"])
        self.assertEqual(outcome["aborted"], [])
        self.assertEqual(outcome["state"], {"1": "a"})

    def test_explicit_abort_takes_no_part_in_conflicts(self):
        # An aborted transaction leaves no edges behind and cannot
        # cause another transaction to be cancelled.
        schedule = {
            "initial": {"1": 0},
            "ops": [
                {"op": "begin", "tx": "A"},
                {"op": "begin", "tx": "B"},
                {"op": "get", "tx": "A", "key": 1},
                {"op": "get", "tx": "B", "key": 1},
                {"op": "abort", "tx": "A"},
                {"op": "put", "tx": "B", "key": 1, "value": 1},
                {"op": "commit", "tx": "B"},
            ]}
        results, outcome = run_schedule(schedule)
        self.assertEqual(outcome["committed"], ["B"])
        self.assertEqual(outcome["aborted"], ["A"])
        self.assertEqual(outcome["state"], {"1": 1})


if __name__ == "__main__":
    unittest.main()
