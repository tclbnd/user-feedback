import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from deadlock import DeadlockDetector  # noqa: E402
from lockmgr import LockTable  # noqa: E402
from locksim import run  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def load(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return json.load(fh)


def events_of(out, kind):
    return [e for e in out["events"] if e["event"] == kind]


class WaitForGraphTests(unittest.TestCase):
    def test_edges_track_holders_and_queue(self):
        lt = LockTable()
        d = DeadlockDetector(lt)
        lt.request(1, "r", "X")
        lt.request(2, "r", "S")
        d.add_waiter(2)
        self.assertEqual(d.graph[2], {1})
        # A later S request is compatible with the queued S in front of it,
        # so it only inherits the edge to the X holder (edges require an
        # incompatible mode, per the README).
        lt.request(3, "r", "S")
        d.add_waiter(3)
        self.assertEqual(d.graph[3], {1})
        # An X request behind the queued S requests gets an edge to the
        # holder and to every incompatible queue entry ahead of it.
        lt.request(4, "r", "X")
        d.add_waiter(4)
        self.assertEqual(d.graph[4], {1, 2, 3})

    def test_graph_shrinks_after_release(self):
        lt = LockTable()
        d = DeadlockDetector(lt)
        lt.request(1, "r", "X")
        lt.request(2, "r", "S")
        d.add_waiter(2)
        woken, touched = lt.release_all(1)
        d.refresh_resources(touched)
        self.assertEqual([w[0] for w in woken], [2])
        d.remove(2)
        self.assertEqual(d.graph, {})

    def test_cycle_search_is_root_scoped_and_deterministic(self):
        lt = LockTable()
        d = DeadlockDetector(lt)
        # Independent cycle 1<->2, while 3 only waits on 1.
        lt.request(1, "a", "X")
        lt.request(2, "b", "X")
        lt.request(2, "a", "X")
        lt.request(1, "b", "X")
        lt.request(3, "a", "S")
        d.add_waiter(2)
        d.add_waiter(1)
        d.add_waiter(3)
        self.assertIsNone(d.find_cycle(3))
        cycle = d.find_cycle(1)
        self.assertIsNotNone(cycle)
        self.assertEqual(sorted(cycle), [1, 2])
        self.assertEqual(d.find_cycle(1), cycle)  # deterministic


class ScriptedCycleTests(unittest.TestCase):
    def test_two_party_cycle(self):
        out = run(load("cycle2.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(len(deadlocks), 1)
        self.assertEqual(deadlocks[0]["cycle"], [1, 2])
        self.assertEqual(deadlocks[0]["victim"], 2)
        self.assertEqual(out["aborted"], [2])
        self.assertEqual(out["committed"], [1])
        self.assertEqual(out["waiting"], [])

    def test_three_party_cycle(self):
        out = run(load("cycle3.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual([e["cycle"] for e in deadlocks], [[1, 2, 3]])
        # All wrote zero rows: the latest beginner (tx 3) is the victim.
        self.assertEqual(deadlocks[0]["victim"], 3)
        self.assertEqual(out["aborted"], [3])
        # Survivors finish in wake-up grant order: 2 then 1.
        self.assertEqual(out["committed"], [2, 1])

    def test_upgrade_cycle(self):
        out = run(load("cycle_upgrade.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(len(deadlocks), 1)
        self.assertEqual(deadlocks[0]["cycle"], [1, 2])
        self.assertEqual(deadlocks[0]["victim"], 2)
        grants = [(e["tx"], e["mode"]) for e in events_of(out, "granted")]
        # The surviving S -> X conversion is granted as X before commit.
        self.assertIn((1, "X"), grants)
        self.assertEqual(out["committed"], [1])
        self.assertEqual(out["aborted"], [2])

    def test_intention_lock_cycle(self):
        out = run(load("cycle_intention.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(len(deadlocks), 1)
        self.assertEqual(deadlocks[0]["cycle"], [1, 2, 3])
        self.assertEqual(deadlocks[0]["victim"], 3)
        self.assertEqual(out["aborted"], [3])
        # The cycle is broken: no further deadlock event may be reported even
        # though FIFO leaves 1 and 2 waiting for later releases.
        self.assertEqual(sorted(out["waiting"]), [1, 2])
        # Every edge in that cycle was produced by intention-lock modes.
        waiting = {e["tx"]: e["mode"] for e in events_of(out, "waiting")}
        self.assertEqual(waiting, {3: "S", 1: "IX", 2: "IX"})

    def test_victim_fewest_written_rows_wins_over_begin_time(self):
        out = run(load("victim_writes.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(deadlocks[0]["cycle"], [1, 2])
        # tx 2 began first but only wrote one row; tx 1 wrote three.
        self.assertEqual(deadlocks[0]["victim"], 2)
        self.assertEqual(out["aborted"], [2])
        self.assertEqual(out["committed"], [1])

    def test_acyclic_load_has_no_false_positive(self):
        for script in ("acyclic.json", "intention_compat.json"):
            with self.subTest(script=script):
                out = run(load(script))
                self.assertEqual(events_of(out, "deadlock"), [])
                self.assertEqual(out["aborted"], [])
                self.assertEqual(out["waiting"], [])

    def test_orders_workload_detects_its_real_three_party_cycle(self):
        # The bundled multi-table update workload genuinely deadlocks.
        out = run(load("orders.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual([e["cycle"] for e in deadlocks], [[1, 2, 3]])
        self.assertEqual(deadlocks[0]["victim"], 3)
        self.assertEqual(out["committed"], [2, 1])
        self.assertEqual(out["aborted"], [3])
        self.assertEqual(out["waiting"], [])

    def test_no_timeout_events_exist_anymore(self):
        out = run(load("acyclic.json"))
        self.assertNotIn("timeout", {e["event"] for e in out["events"]})


class UpgradeQueueExampleTests(unittest.TestCase):
    def test_bundled_upgrade_queue_scenario(self):
        out = run(load("upgrade_queue.json"))
        deadlocks = events_of(out, "deadlock")
        self.assertEqual([e["cycle"] for e in deadlocks], [[1, 2]])
        self.assertEqual(deadlocks[0]["victim"], 2)
        # The waiting X (tx 3) is granted before the later S (tx 4): no
        # starvation of the exclusive request.
        self.assertEqual(out["committed"], [1, 3, 4])
        self.assertEqual(out["aborted"], [2])


class EdgeCaseTests(unittest.TestCase):
    def test_chain_wait_has_no_cycle_and_no_aborts(self):
        # 1 -> 2 -> 3 style waiting on two resources, never a cycle.
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 3, "op": "begin"},
            {"tx": 1, "op": "lock", "res": "a", "mode": "X"},
            {"tx": 2, "op": "lock", "res": "a", "mode": "S"},
            {"tx": 2, "op": "lock", "res": "b", "mode": "X"},
            {"tx": 3, "op": "lock", "res": "b", "mode": "S"},
            {"tx": 1, "op": "commit"},
            {"tx": 2, "op": "commit"},
            {"tx": 3, "op": "commit"},
        ]
        out = run({"ops": ops})
        self.assertEqual(events_of(out, "deadlock"), [])
        # tx2 is woken first on "a"; its queued X on "b" still blocks behind
        # its own holder, so tx3 (S on "b") releases first: FIFO wake order.
        self.assertEqual(out["committed"], [1, 3, 2])
        self.assertEqual(out["aborted"], [])

    def test_explicit_abort_releases_locks_and_wakes_queue(self):
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 1, "op": "write", "res": "r"},
            {"tx": 2, "op": "write", "res": "r"},
            {"tx": 1, "op": "abort"},
            {"tx": 2, "op": "commit"},
        ]
        out = run({"ops": ops})
        self.assertEqual(events_of(out, "deadlock"), [])
        self.assertEqual(out["aborted"], [1])
        self.assertEqual(out["committed"], [2])

    def test_repeated_resolution_breaks_nested_cycle(self):
        # Two disjoint cycles formed at once: the detector must keep aborting
        # only while the blocked requester is on a cycle.
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 3, "op": "begin"}, {"tx": 4, "op": "begin"},
            {"tx": 1, "op": "write", "res": "a"},
            {"tx": 2, "op": "write", "res": "b"},
            {"tx": 3, "op": "write", "res": "c"},
            {"tx": 4, "op": "write", "res": "d"},
            {"tx": 2, "op": "write", "res": "a"},
            {"tx": 1, "op": "write", "res": "b"},
            {"tx": 4, "op": "write", "res": "c"},
            {"tx": 3, "op": "write", "res": "d"},
            {"tx": 1, "op": "commit"}, {"tx": 2, "op": "commit"},
            {"tx": 3, "op": "commit"}, {"tx": 4, "op": "commit"},
        ]
        out = run({"ops": ops})
        deadlocks = events_of(out, "deadlock")
        # Two independent cycles: each is resolved only when one of its own
        # members blocks, never by a speculative abort from the other cycle.
        self.assertEqual(len(deadlocks), 2)
        self.assertEqual(sorted(deadlocks[0]["cycle"]), [1, 2])
        self.assertEqual(sorted(deadlocks[1]["cycle"]), [3, 4])
        self.assertEqual(len(out["aborted"]), 2)
        self.assertEqual(len(out["committed"]), 2)

    def test_exclusive_request_not_starved_end_to_end(self):
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 3, "op": "begin"}, {"tx": 4, "op": "begin"},
            {"tx": 1, "op": "lock", "res": "r", "mode": "S"},
            {"tx": 2, "op": "lock", "res": "r", "mode": "X"},
            {"tx": 3, "op": "lock", "res": "r", "mode": "S"},
            {"tx": 4, "op": "lock", "res": "r", "mode": "S"},
            {"tx": 1, "op": "commit"},
            {"tx": 2, "op": "commit"},
            {"tx": 3, "op": "commit"},
            {"tx": 4, "op": "commit"},
        ]
        out = run({"ops": ops})
        grants = [e["tx"] for e in out["events"] if e["event"] == "granted"]
        # After tx1 releases, tx2's X is granted before tx3/tx4 despite the
        # later S requests being mutually compatible.
        self.assertEqual(grants, [1, 2, 3, 4])
        self.assertEqual(out["committed"], [1, 2, 3, 4])

    def test_write_counts_observed_only_after_grant(self):
        # Both wrote zero rows at detection time: latest beginner is victim.
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 1, "op": "write", "res": "a"},
            {"tx": 2, "op": "write", "res": "b"},
            {"tx": 1, "op": "write", "res": "b"},
            {"tx": 2, "op": "write", "res": "a"},
            {"tx": 1, "op": "commit"}, {"tx": 2, "op": "commit"},
        ]
        out = run({"ops": ops})
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(deadlocks[0]["victim"], 2)
        # The blocked write of the survivor is counted when its lock grants,
        # so tx1 ends with two written rows.
        self.assertEqual(len([e for e in out["events"]
                              if e["event"] == "granted" and e["tx"] == 1
                              and e["mode"] == "X"]), 2)

    def test_victim_tie_breaks_on_latest_begin(self):
        # Each cycle member wrote exactly one row: the latest beginner loses.
        ops = [
            {"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
            {"tx": 3, "op": "begin"},
            {"tx": 1, "op": "write", "res": "a"},
            {"tx": 2, "op": "write", "res": "b"},
            {"tx": 3, "op": "write", "res": "c"},
            {"tx": 1, "op": "write", "res": "b"},
            {"tx": 2, "op": "write", "res": "a"},
            {"tx": 1, "op": "commit"}, {"tx": 2, "op": "commit"},
            {"tx": 3, "op": "commit"},
        ]
        out = run({"ops": ops})
        deadlocks = events_of(out, "deadlock")
        self.assertEqual(deadlocks[0]["cycle"], [1, 2])
        self.assertEqual(deadlocks[0]["victim"], 2)


if __name__ == "__main__":
    unittest.main()
