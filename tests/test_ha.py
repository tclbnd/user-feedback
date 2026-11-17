import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ha  # noqa: E402
from controller import Controller  # noqa: E402
from node import Node  # noqa: E402


def load_example(name):
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as fh:
        return json.load(fh)


def run_spec(spec):
    """Run a scenario in-process and return (cluster, parsed outputs, problems)."""
    cluster = ha.Cluster(spec)
    for t in range(1, spec["ticks"] + 1):
        cluster.tick(t)
    with tempfile.TemporaryDirectory() as d:
        cluster.save(d)
        trace, state, conflicts, summary = ha.load(d)
    return cluster, (trace, state, conflicts, summary), ha.check(trace, state, conflicts, summary)


def accepted(trace):
    return [r for r in trace if "event" not in r and r["result"] == "ok"]


class NodeTests(unittest.TestCase):
    def test_lagging_replica_catches_up(self):
        leader, replica = Node("n1"), Node("n2")
        leader.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 1, "key": "b", "value": "2"}]
        replica.log = leader.log[:1]
        self.assertTrue(replica.receive(leader))
        self.assertEqual(replica.kv(), {"a": "1", "b": "2"})
        self.assertEqual(replica.conflicts, [])

    def test_fork_point_stops_at_first_differing_term(self):
        a, b = Node("n1"), Node("n2")
        a.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 3, "key": "x", "value": "mine"}]
        b.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 2, "key": "b", "value": "theirs"}]
        self.assertEqual(a.fork_point(b.log), 1)

    def test_receiver_rolls_back_the_tail_and_exports_it(self):
        leader, replica = Node("n1"), Node("n2")
        leader.role, leader.term = "primary", 2
        leader.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 2, "key": "b", "value": "new"}]
        replica.term = 1
        replica.log = [{"term": 1, "key": "a", "value": "1"},
                       {"term": 1, "key": "x", "value": "old1"},
                       {"term": 1, "key": "x", "value": "old2"}]
        record = replica.receive(leader)
        self.assertEqual(record["fork"], 1)
        self.assertEqual(replica.kv(), {"a": "1", "b": "new"})
        self.assertEqual([(c["index"], c["key"], c["value"]) for c in replica.conflicts],
                         [(2, "x", "old1"), (3, "x", "old2")])
        self.assertEqual(replica.term, 2)

    def test_storage_rejects_stale_term(self):
        stale, current = Node("n1"), Node("n2")
        stale.log = [{"term": 1, "key": "x", "value": "old"}]
        current.role, current.term = "primary", 3
        current.log = [{"term": 3, "key": "y", "value": "new"}]
        self.assertFalse(current.receive(stale))
        self.assertEqual(current.log, [{"term": 3, "key": "y", "value": "new"}])
        self.assertEqual(current.conflicts, [])

    def test_lease_ends_before_the_controller_can_promote(self):
        # Slow clock: local 8.0 at t=10 equals the deadline, so the holder must
        # already be out of lease on the very tick the controller can promote.
        slow = Node("n1", rate=0.8)
        slow.grant_lease(0)                              # lease 10, drift 0.2
        self.assertTrue(slow.lease_valid(9, 10, 0.2))    # local 7.2 < 8.0
        self.assertFalse(slow.lease_valid(10, 10, 0.2))  # local 8.0 is the deadline
        # Exact clock with drift: same boundary, no extra tick from rounding.
        exact = Node("n2", rate=1.0)
        exact.grant_lease(0)
        self.assertTrue(exact.lease_valid(7, 10, 0.2))
        self.assertFalse(exact.lease_valid(8, 10, 0.2))
        # No lease at all (e.g. after a restart) never counts as writable.
        fresh = Node("n3")
        self.assertFalse(fresh.writable(1, 10, 0.2))


class ControllerTests(unittest.TestCase):
    def test_elect_picks_term_then_length_then_name(self):
        a, b, c = Node("n1"), Node("n2"), Node("n3")
        a.log = [{"term": 1, "key": "k", "value": "1"}] * 5
        b.log = [{"term": 1, "key": "k", "value": "1"}] * 4 + [{"term": 2, "key": "k", "value": "2"}]
        c.log = [{"term": 1, "key": "k", "value": "1"}] * 4 + [{"term": 2, "key": "k", "value": "3"}]
        ctl = Controller(lease=5)
        # b and c tie on (term of last entry, length); the smaller name wins.
        self.assertIs(ctl.elect([a, b, c]), b)

    def test_elect_prefers_the_longer_log_with_the_same_term(self):
        a, b = Node("n1"), Node("n2")
        a.log = [{"term": 1, "key": "k", "value": "1"}]
        b.log = [{"term": 1, "key": "k", "value": "1"}, {"term": 2, "key": "k", "value": "2"}]
        self.assertIs(Controller(lease=5).elect([a, b]), b)


class ScenarioTests(unittest.TestCase):
    def test_steady_cluster(self):
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 5, "ticks": 6,
                "writes": [{"t": t, "node": "n1", "key": "k", "value": str(t)} for t in range(1, 5)]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as fh:
                json.dump(spec, fh)
            subprocess.run([sys.executable, os.path.join(ROOT, "ha.py"), "run", path, os.path.join(d, "out")],
                           check=True, capture_output=True)
            with open(os.path.join(d, "out", "state.json")) as fh:
                state = json.load(fh)
        self.assertEqual({n: s["kv"] for n, s in state.items()}, {n: {"k": "4"} for n in ("n1", "n2", "n3")})

    def test_cli_reports_declared_checks(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "out")
            subprocess.run([sys.executable, os.path.join(ROOT, "ha.py"), "run",
                            os.path.join(ROOT, "examples", "partition_writes.json"), out],
                           check=True, capture_output=True)
            result = subprocess.run([sys.executable, os.path.join(ROOT, "ha.py"), "check", out],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok", result.stdout)

    def test_writes_during_partition_are_fenced_and_exported(self):
        cluster, (trace, state, conflicts, _), problems = run_spec(
            load_example("partition_writes.json"))
        self.assertEqual(problems, [])
        # The isolated primary stops writing once its lease runs out.
        n1_writes = [r for r in trace if r.get("node") == "n1" and "event" not in r]
        self.assertTrue(any(r["result"] == "rejected" for r in n1_writes))
        # The controller promoted n2, the node with the log that has a term 1
        # entry and then the longest log of the reachable pair.
        self.assertTrue(any(r.get("event") == "promote" and r["node"] == "n2" for r in trace))
        # n1's partition writes are exported, not silently merged.
        exported = [(c["index"], c["key"], c["value"]) for c in conflicts["n1"]]
        self.assertEqual(exported, [(2, "x", "x1"), (3, "x", "x2"), (4, "x", "x3")])
        # Everyone converges on the surviving branch.
        kvs = {n: st["kv"] for n, st in state.items()}
        self.assertEqual(kvs, {n: {"a": "1", "b": "b3"} for n in ("n1", "n2", "n3")})

    def test_double_fault_mid_failover(self):
        cluster, (trace, state, conflicts, _), problems = run_spec(
            load_example("failover_double_fault.json"))
        self.assertEqual(problems, [])
        promotes = [r for r in trace if r.get("event") == "promote"]
        self.assertGreaterEqual(len(promotes), 2)
        self.assertEqual([r["term"] for r in promotes], sorted(r["term"] for r in promotes))
        kvs = {n: st["kv"] for n, st in state.items()}
        self.assertEqual(len({json.dumps(kv, sort_keys=True) for kv in kvs.values()}), 1)
        self.assertTrue(conflicts["n1"] and conflicts["n2"])

    def test_late_rejoin_rolls_back_the_local_branch(self):
        cluster, (trace, state, conflicts, _), problems = run_spec(
            load_example("late_rejoin.json"))
        self.assertEqual(problems, [])
        exported = [(c["index"], c["key"], c["value"]) for c in conflicts["n1"]]
        self.assertEqual(exported, [(2, "x", "x1"), (3, "x", "x2"), (4, "x", "x3"),
                                    (5, "x", "x4"), (6, "x", "late")])
        self.assertEqual(state["n1"]["role"], "replica")
        self.assertNotIn("x", state["n1"]["kv"])
        self.assertEqual(state["n1"]["kv"], state["n2"]["kv"])

    def test_old_primary_stops_accepting_before_it_is_replaced(self):
        cluster, (trace, state, conflicts, summary), problems = run_spec(
            load_example("delayed_rejoin.json"))
        self.assertEqual(problems, [])
        promote = next(r for r in trace if r.get("event") == "promote")
        # The old primary is out of lease on the very tick the controller
        # promotes, so it can neither accept a write nor keep pushing.
        self.assertFalse([r for r in accepted(trace) if r["node"] == "n1" and r["t"] >= promote["t"]])
        self.assertTrue([r for r in trace if r.get("event") == "step_down" and r["node"] == "n1"
                         and r["t"] <= promote["t"]])
        # Writes it did accept while alone are exported, not silently merged.
        self.assertEqual([(c["index"], c["key"], c["value"]) for c in conflicts["n1"]],
                         [(2, "x", "x1"), (3, "x", "x2")])
        self.assertEqual(state["n1"]["kv"], state["n2"]["kv"])

    def test_healed_old_primary_obeys_the_new_term(self):
        # n1 is cut off, n2 takes over, and the partition heals while n1 is
        # still up.  n1 must adopt the new term and refuse every later write
        # even though its log looked locally fine.
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 4, "drift": 0.2, "ticks": 14,
                "events": [{"t": 2, "partition": [["n1"], ["n2", "n3", "ctl"]]},
                           {"t": 7, "heal": True}],
                "writes": [{"t": 1, "node": "n1", "key": "a", "value": "1"},
                           {"t": 3, "node": "n1", "key": "x", "value": "x1"},
                           {"t": 9, "node": "n1", "key": "z", "value": "late"},
                           {"t": 10, "node": "n2", "key": "b", "value": "b1"}]}
        _, (trace, state, conflicts, _), problems = run_spec(spec)
        self.assertEqual(problems, [])
        late = [r for r in trace if "event" not in r and r["key"] == "z"]
        self.assertEqual([r["result"] for r in late], ["rejected"])
        self.assertEqual(state["n1"]["term"], state["n2"]["term"])
        self.assertEqual(state["n1"]["kv"], {"a": "1", "b": "b1"})
        self.assertEqual([(c["key"], c["value"]) for c in conflicts["n1"]], [("x", "x1")])

    def test_slow_clock_never_accepts_after_the_controller_can_promote(self):
        spec = load_example("slow_clock.json")
        cluster, (trace, state, conflicts, summary), problems = run_spec(spec)
        self.assertEqual(problems, [])
        promote = next(r for r in trace if r.get("event") == "promote")
        # From the promotion tick on, the old primary accepts nothing.
        late = [r for r in accepted(trace) if r["node"] == "n1" and r["t"] >= promote["t"]]
        self.assertEqual(late, [])
        self.assertEqual([(c["index"], c["key"], c["value"]) for c in conflicts["n1"]],
                         [(2, "x", "n1-10"), (3, "x", "n1-11")])


class InvariantTests(unittest.TestCase):
    def test_checks_catch_a_second_writer(self):
        trace = [{"t": 1, "node": "n1", "key": "a", "value": "1", "result": "ok"},
                 {"t": 1, "node": "n2", "key": "a", "value": "2", "result": "ok"}]
        state = {"n1": {"role": "primary", "term": 1, "alive": True, "log": [[1, "a", "1"]], "kv": {"a": "1"}},
                 "n2": {"role": "primary", "term": 1, "alive": True, "log": [[1, "a", "2"]], "kv": {"a": "2"}}}
        problems = ha.check(trace, state, {"n1": [], "n2": []}, {})
        self.assertTrue(any("accepted by" in p for p in problems))
        self.assertTrue(any("diverging" in p for p in problems))

    def test_checks_catch_a_bad_conflict_record(self):
        trace, state = [], {"n1": {"role": "replica", "term": 2, "alive": True,
                                   "log": [[1, "a", "1"], [2, "b", "2"]], "kv": {"a": "1", "b": "2"}}}
        conflicts = {"n1": [{"index": 2, "term": 2, "key": "b", "value": "2"},
                            {"index": 4, "term": 1, "key": "x", "value": "x"}]}
        problems = ha.check(trace, state, conflicts, {})
        self.assertTrue(any("conflict file does not match" in p for p in problems))

    def test_randomized_scenarios_hold_the_invariants(self):
        import random

        def gen(seed):
            rnd = random.Random(seed)
            names = ["n1", "n2", "n3"]
            lease = rnd.choice([3, 4, 5, 6, 8])
            drift = rnd.choice([0.0, 0.1, 0.2])
            rates = {n: round(rnd.uniform(1 - drift, 1 + drift), 3)
                     for n in names if rnd.random() < 0.5}
            ticks = rnd.randint(20, 30)
            events, t = [], rnd.randint(2, 4)
            for _ in range(rnd.randint(1, 3)):
                if rnd.random() < 0.7:
                    side = rnd.sample(names, rnd.randint(1, len(names) - 1))
                    other = [n for n in names if n not in side]
                    groups = [side + ["ctl"], other] if rnd.random() < 0.5 else [side, other + ["ctl"]]
                    events.append({"t": t, "partition": groups})
                else:
                    victim = rnd.choice(names)
                    events.append({"t": t, "crash": victim})
                    events.append({"t": t + rnd.randint(1, 5), "restart": victim})
                t += rnd.randint(1, 6)
            events.append({"t": t, "heal": True})
            writes = [{"t": tt, "node": rnd.choice(names), "key": rnd.choice("abcxyz"),
                       "value": "s{0}-{1}".format(seed, tt)}
                      for tt in range(1, ticks + 1) if rnd.random() < 0.55]
            return {"nodes": names, "lease": lease, "drift": drift, "clock_rate": rates,
                    "ticks": ticks, "events": events, "writes": writes}

        failures = []
        for seed in range(200):
            _, _, problems = run_spec(gen(seed))
            if problems:
                failures.append((seed, problems))
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
