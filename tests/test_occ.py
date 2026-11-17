import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kv import Store  # noqa: E402
from occ import Engine  # noqa: E402
from occsim import run_schedule  # noqa: E402


def serial_replay(schedule, outputs):
    """Replay a schedule as if each transaction ran atomically.

    Every committed update transaction takes effect at its commit point and
    every transaction reads the state as of its begin, so replaying the
    committed updates in commit order yields the final state a serial
    execution would produce.  The recorded gets and scans are checked against
    the reads the replay expects, so a schedule that observed a non-serial
    state fails here even when the commit flags look right.
    """
    stream = list(outputs)
    observed, commits = {}, {}
    for step in schedule:
        if step["op"] == "dump":
            stream.pop(0)
        elif step["op"] in ("get", "scan", "commit"):
            record = stream.pop(0)
            if step["op"] == "commit":
                commits[step["s"]] = record["result"]
            else:
                observed.setdefault(step["s"], []).append(record)
    if stream:
        raise AssertionError("unconsumed output records")

    # Where each transaction sits in the serial order: an update transaction
    # at its commit, a read-only one at its begin.
    positions = {}
    for index, step in enumerate(schedule):
        if step["op"] == "dump":
            continue
        s = step["s"]
        positions.setdefault(s, {})[step["op"]] = index
    order = []
    for s, ops in positions.items():
        if "put" in ops or "delete" in ops:
            if commits[s] == "committed":
                order.append((ops["commit"], s))
        else:
            order.append((ops["begin"], s))
    order.sort()

    state, reads = {}, {}
    for _, s in order:
        view = dict(state)
        for step in schedule:
            if step.get("s") != s:
                continue
            if step["op"] == "get":
                reads.setdefault(s, []).append(view.get(step["key"]))
            elif step["op"] == "scan":
                reads.setdefault(s, []).append(Store.scan(view, step["lo"], step["hi"]))
            elif step["op"] == "put":
                view[step["key"]] = step["value"]
            elif step["op"] == "delete":
                view.pop(step["key"], None)
        # The replay's own reads must match what the transaction observed.
        for want, record in zip(reads.get(s, []), observed.get(s, [])):
            got = record["value"] if record["op"] == "get" else [tuple(i) for i in record["items"]]
            if record["op"] == "scan":
                want = sorted(want)
            if want != got:
                raise AssertionError(f"serial replay disagrees with {s}: {want} != {got}")
        if commits[s] == "committed":
            state = view
    return state


class OccTests(unittest.TestCase):
    def assertSerializable(self, schedule, expected_results):
        """Run `schedule`; check commit results and serial equivalence."""
        outputs = run_schedule(schedule)
        results = [r["result"] for r in outputs if r["op"] == "commit"]
        self.assertEqual(results, expected_results)
        dump = [r for r in outputs if r["op"] == "dump"][0]["items"]
        self.assertEqual(dump, [list(i) for i in sorted(serial_replay(schedule, outputs).items())])
        return outputs

    def test_sequential_transactions_commit(self):
        e = Engine()
        t = e.begin(); t.put("a", "1"); self.assertEqual(e.commit(t), "committed")
        t = e.begin(); self.assertEqual(t.get("a"), "1"); t.put("b", "2")
        self.assertEqual(e.commit(t), "committed")
        self.assertEqual(e.store.data, {"a": "1", "b": "2"})

    def test_lost_update_is_rejected(self):
        e = Engine()
        t0 = e.begin(); t0.put("x", "0"); e.commit(t0)
        t1, t2 = e.begin(), e.begin()
        t1.get("x"); t2.get("x")
        t1.put("x", "1"); t2.put("x", "2")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "aborted"])

    def test_read_conflict_aborts(self):
        # T2 reads a row, T1 overwrites it, T2 must not commit a stale write.
        e = Engine()
        t1, t2 = e.begin(), e.begin()
        t1.get("x"); t2.get("x")
        t1.put("x", "1"); t2.put("x", "2")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "aborted"])
        self.assertEqual(e.store.data, {"x": "1"})

    def test_blind_write_write_commits_in_commit_order(self):
        # Neither transaction read the row, so the later commit is simply
        # the later write in the serial order -- no read set to invalidate.
        e = Engine()
        t1, t2 = e.begin(), e.begin()
        t1.put("x", "1"); t2.put("x", "2")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "committed"])
        self.assertEqual(e.store.data, {"x": "2"})

    def test_range_insert_phantom_aborts(self):
        # T2 scanned [a, c); T1 inserted a row inside that range that T2
        # never saw, so T2's scan is no longer valid.
        e = Engine()
        t0 = e.begin()
        t0.put("a1", "free"); t0.put("a2", "free")
        e.commit(t0)
        t1, t2 = e.begin(), e.begin()
        self.assertEqual([k for k, _ in t2.scan("a", "c")], ["a1", "a2"])
        t1.put("a3", "free")
        self.assertEqual(e.commit(t1), "committed")
        t2.put("report", "2 free")
        self.assertEqual(e.commit(t2), "aborted")
        self.assertEqual(sorted(e.store.data), ["a1", "a2", "a3"])

    def test_empty_range_phantom_aborts(self):
        # The scan returned nothing, so there is no row in the read set: only
        # the recorded predicate can catch the concurrent insert.
        e = Engine()
        t1, t2 = e.begin(), e.begin()
        self.assertEqual(t2.scan("a", "c"), [])
        t1.put("a1", "free")
        self.assertEqual(e.commit(t1), "committed")
        t2.put("report", "no seats in row a")
        self.assertEqual(e.commit(t2), "aborted")
        self.assertEqual(e.store.data, {"a1": "free"})

    def test_range_insert_outside_scan_commits(self):
        e = Engine()
        t1, t2 = e.begin(), e.begin()
        t2.scan("a", "c")
        t1.put("m1", "free")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "committed"])
        self.assertEqual(sorted(e.store.data), ["m1"])

    def test_scan_range_is_half_open(self):
        e = Engine()
        t0 = e.begin(); t0.put("c", "1"); e.commit(t0)
        t1, t2 = e.begin(), e.begin()
        t2.scan("a", "c")  # "c" itself lies outside the range
        t1.put("c", "2")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "committed"])

    def test_delete_phantom_aborts(self):
        # T2 scanned [a, c) and reported on it; T1 deleted a row inside the
        # range, so the report is stale.
        e = Engine()
        t0 = e.begin()
        t0.put("a1", "free"); t0.put("a2", "free"); t0.put("a3", "free")
        e.commit(t0)
        t1, t2 = e.begin(), e.begin()
        t2.scan("a", "c")
        t1.delete("a2")
        self.assertEqual(e.commit(t1), "committed")
        t2.put("report", "3 free")
        self.assertEqual(e.commit(t2), "aborted")
        self.assertEqual(sorted(e.store.data), ["a1", "a3"])

    def test_delete_phantom_of_unseen_row_aborts(self):
        # The deleted row was not in T2's read set: it was inserted after
        # T2's scan.  Only the range predicate can catch this.
        e = Engine()
        t1, t2, t3 = e.begin(), e.begin(), e.begin()
        t2.scan("a", "c")
        t1.put("a3", "free")
        self.assertEqual(e.commit(t1), "committed")
        t3.delete("a3")
        self.assertEqual(e.commit(t3), "committed")
        t2.put("report", "2 free")
        self.assertEqual(e.commit(t2), "aborted")
        self.assertEqual(e.store.data, {})

    def test_deleted_key_is_gone_for_owner_and_readers(self):
        e = Engine()
        t0 = e.begin(); t0.put("a1", "free"); e.commit(t0)
        t1 = e.begin(); t1.delete("a1")
        self.assertIsNone(t1.get("a1"))
        self.assertEqual(t1.scan("a", "b"), [])
        self.assertEqual(e.commit(t1), "committed")
        self.assertEqual(e.store.data, {})
        t2 = e.begin()
        self.assertIsNone(t2.get("a1"))
        self.assertEqual(t2.scan("a", "b"), [])
        self.assertEqual(e.commit(t2), "committed")

    def test_read_only_fast_path_ignores_writes_after_begin(self):
        # Serialized at begin: a concurrent write does not abort it.
        e = Engine()
        t0 = e.begin(); t0.put("a1", "free"); e.commit(t0)
        ro, w = e.begin(), e.begin()
        ro.scan("a", "b")
        w.put("a1", "taken")
        self.assertEqual(e.commit(w), "committed")
        self.assertEqual(e.commit(ro), "committed")
        self.assertEqual(e.commit_no, 2)  # read-only did not advance it

    def test_atomic_validation_and_install_under_threads(self):
        # Many transactions incrementing one counter: each attempt reads the
        # counter and writes back the increment, so two attempts that both
        # validate against the same log and then install would lose an
        # increment.  The final value must equal the number of attempts that
        # reported "committed".
        import sys
        import threading

        e = Engine()
        t0 = e.begin(); t0.put("n", "0"); e.commit(t0)
        workers, rounds, max_tries = 8, 25, 5000
        successes = []
        lock = threading.Lock()
        barrier = threading.Barrier(workers)
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)

        def worker():
            barrier.wait()
            won = 0
            for _ in range(rounds):
                for _ in range(max_tries):
                    t = e.begin()
                    value = int(t.get("n"))
                    t.put("n", str(value + 1))
                    if e.commit(t) == "committed":
                        won += 1
                        break
                else:
                    self.fail("no attempt committed within the retry budget")
            with lock:
                successes.append(won)

        try:
            threads = [threading.Thread(target=worker) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(interval)
        self.assertEqual(e.store.data["n"], str(sum(successes)))

    def test_schedule_read_conflict(self):
        schedule = [
            {"op": "begin", "s": "A"},
            {"op": "begin", "s": "B"},
            {"op": "get", "s": "A", "key": "x"},
            {"op": "get", "s": "B", "key": "x"},
            {"op": "put", "s": "A", "key": "x", "value": "1"},
            {"op": "put", "s": "B", "key": "x", "value": "2"},
            {"op": "commit", "s": "A"},
            {"op": "commit", "s": "B"},
            {"op": "dump"},
        ]
        self.assertSerializable(schedule, ["committed", "aborted"])

    def test_schedule_no_conflict_concurrency(self):
        schedule = [
            {"op": "begin", "s": "A"},
            {"op": "begin", "s": "B"},
            {"op": "scan", "s": "A", "lo": "a", "hi": "c"},
            {"op": "put", "s": "A", "key": "a1", "value": "1"},
            {"op": "put", "s": "B", "key": "m1", "value": "2"},
            {"op": "get", "s": "B", "key": "a1"},
            {"op": "commit", "s": "B"},
            {"op": "commit", "s": "A"},
            {"op": "dump"},
        ]
        self.assertSerializable(schedule, ["committed", "committed"])

    def test_example_schedules_are_serializable(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cases = {
            "phantom_insert.json": ["committed", "committed", "aborted"],
            "phantom_delete.json": ["committed", "committed", "aborted", "committed"],
            "readonly.json": ["committed", "committed", "committed"],
            "read_conflict.json": ["committed", "committed", "aborted"],
            "no_conflict.json": ["committed", "committed", "committed"],
        }
        for name, results in cases.items():
            with open(os.path.join(here, "examples", name), encoding="utf-8") as fh:
                schedule = json.load(fh)
            with self.subTest(name=name):
                self.assertSerializable(schedule, results)


if __name__ == "__main__":
    unittest.main()

