import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from controller import Controller  # noqa: E402
from ha import Cluster, ScenarioError, validate  # noqa: E402
from node import Node  # noqa: E402


def drift_edge(drift, rng):
    """Pin a clock rate to one of the drift bounds (never beyond)."""
    return round(rng.choice([1 - drift, 1 + drift]), 12)


def run_cluster(spec):
    """Simulate `spec` in-process and return the audit report."""
    validate(spec)
    cluster = Cluster(spec)
    for t in range(1, spec["ticks"] + 1):
        cluster.tick(t)
    return cluster, cluster.audit()


class NodeTests(unittest.TestCase):
    def test_lease_boundary_is_conservative_for_slow_clock(self):
        node = Node("n1", rate=0.8)
        node.lease_start = node.local(0)
        # Margin is 10 * (1 - 0.2) = 8.0 of local time == 10 ticks at 0.8.
        self.assertTrue(node.lease_valid(9, 10, 0.2))
        self.assertFalse(node.lease_valid(10, 10, 0.2))
        self.assertFalse(node.lease_valid(11, 10, 0.2))

    def test_lease_expiry_is_strict_at_exact_boundary(self):
        node = Node("n1", rate=1.0)
        node.lease_start = 0.0
        self.assertTrue(node.lease_valid(9, 10, 0.0))
        self.assertFalse(node.lease_valid(10, 10, 0.0))

    def test_stale_term_is_fenced_without_mutation(self):
        replica = Node("n2")
        replica.log = [{"term": 5, "key": "a", "value": "1"}]
        replica.term = 5
        old = Node("n1")
        old.log = [{"term": 2, "key": "b", "value": "2"}]
        old.term = 2
        self.assertFalse(replica.receive(old))
        self.assertEqual(replica.log, [{"term": 5, "key": "a", "value": "1"}])
        self.assertEqual(replica.conflicts, [])

    def test_store_write_rejects_stale_term(self):
        node = Node("n1")
        node.term = 3
        self.assertFalse(node.store_write(2, "k", "v"))
        self.assertEqual(node.log, [])
        self.assertTrue(node.store_write(3, "k", "v"))
        self.assertEqual(node.kv(), {"k": "v"})

    def test_step_down_clears_lease(self):
        node = Node("n1")
        node.role, node.lease_start = "primary", 0.0
        self.assertTrue(node.writable(1, 10, 0.0))
        node.step_down(4)
        self.assertFalse(node.writable(1, 10, 0.0))
        self.assertEqual((node.role, node.term), ("replica", 4))

    def test_fork_point_uses_terms_not_lengths(self):
        leader = Node("n1")
        leader.log = [{"term": 1, "key": "a", "value": "1"},
                      {"term": 3, "key": "b", "value": "2"}]
        follower = Node("n2")
        follower.log = [{"term": 1, "key": "a", "value": "1"},
                        {"term": 2, "key": "x", "value": "old"},
                        {"term": 2, "key": "y", "value": "old"}]
        self.assertEqual(follower.fork_point(leader), 1)


class ElectionTests(unittest.TestCase):
    def test_newest_last_term_beats_longer_stale_log(self):
        stale, fresh = Node("n1"), Node("n2")
        stale.log = [{"term": 1, "key": "k", "value": str(i)} for i in range(5)]
        fresh.log = [{"term": 1, "key": "k", "value": "0"},
                     {"term": 2, "key": "k", "value": "1"}]
        self.assertEqual(Controller(10).elect([stale, fresh]), fresh)

    def test_longer_log_wins_on_equal_last_term(self):
        short, long_ = Node("n1"), Node("n2")
        short.log = [{"term": 2, "key": "k", "value": "a"}]
        long_.log = short.log + [{"term": 2, "key": "k", "value": "b"}]
        self.assertEqual(Controller(10).elect([short, long_]), long_)

    def test_equal_logs_tie_break_on_smaller_name(self):
        a, b = Node("n2"), Node("n1")
        self.assertEqual(Controller(10).elect([a, b]), b)


class SafetyScenarioTests(unittest.TestCase):
    """End-to-end acceptance over controlled partitions and clocks."""

    def test_writes_during_partition_go_only_to_one_side(self):
        # n1 is isolated with an expired lease; n2+n3 elect in the majority
        # side.  n1 keeps receiving client writes from its (now stale) side.
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 4, "drift": 0.2, "ticks": 12,
                "events": [{"t": 2, "partition": [["n1"], ["n2", "n3", "ctl"]]}],
                "writes": [{"t": t, "node": "n1", "key": "x", "value": f"old{t}"}
                           for t in range(2, 12)] +
                          [{"t": t, "node": "n2", "key": "y", "value": f"new{t}"}
                           for t in range(7, 12)]}
        cluster, report = run_cluster(spec)
        self.assertTrue(report["single_writer"], report)
        self.assertEqual(report["violations"], [])
        promoted = [r["node"] for r in cluster.trace if r.get("event") == "promote"]
        self.assertEqual(promoted, ["n2"])
        # Every n1 write after promotion is refused by its expired lease.
        late = [r for r in cluster.trace if "key" in r and r["node"] == "n1" and r["t"] >= 6]
        self.assertTrue(late and all(r["result"] == "rejected" for r in late), late)

    def test_failover_again_while_failing_over(self):
        # Two successive failures: n1 dies, n2 is promoted, then n2 dies just
        # after accepting a write and n3 takes over.  n1 comes back first.
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 4, "drift": 0.2, "ticks": 30,
                "events": [{"t": 2, "crash": "n1"},
                           {"t": 12, "crash": "n2"},
                           {"t": 20, "restart": "n1"},
                           {"t": 24, "restart": "n2"}],
                "writes": [{"t": 1, "node": "n1", "key": "a", "value": "1"},
                           {"t": 9, "node": "n2", "key": "b", "value": "b1"},
                           {"t": 13, "node": "n2", "key": "b", "value": "b2"},
                           {"t": 18, "node": "n3", "key": "c", "value": "c1"},
                           {"t": 26, "node": "n3", "key": "d", "value": "d1"}]}
        cluster, report = run_cluster(spec)
        self.assertTrue(report["single_writer"], report)
        self.assertTrue(report["consistent"], report)
        self.assertEqual(report["primary"], "n3")
        terms = [r["term"] for r in cluster.trace if r.get("event") == "promote"]
        self.assertEqual(terms, sorted(set(terms)))
        self.assertGreaterEqual(len(terms), 2)

    def test_old_primary_rejoins_late_and_rolls_back_fork(self):
        # The key acceptance case: old primary keeps writing while isolated,
        # then rejoins long after the new primary took over.
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 4, "drift": 0.0, "ticks": 24,
                "events": [{"t": 3, "partition": [["n1"], ["n2", "n3", "ctl"]]},
                           {"t": 8, "heal": True}],
                "writes": [{"t": 1, "node": "n1", "key": "a", "value": "1"},
                           {"t": 4, "node": "n1", "key": "a", "value": "old"},
                           {"t": 5, "node": "n1", "key": "a", "value": "older"},
                           {"t": 6, "node": "n2", "key": "b", "value": "new"},
                           {"t": 7, "node": "n2", "key": "b", "value": "newer"}]}
        cluster, report = run_cluster(spec)
        self.assertTrue(report["single_writer"], report)
        self.assertTrue(report["consistent"], report)
        self.assertEqual(report["violations"], [])
        old = cluster.node("n1")
        # The divergent local suffix is exported, not silently merged.
        self.assertEqual([(c["index"], c["key"], c["value"]) for c in old.conflicts],
                         [(2, "a", "old"), (3, "a", "older")])
        self.assertEqual(old.kv(), {"a": "1", "b": "newer"})
        self.assertEqual([e["value"] for e in old.log], ["1", "new", "newer"])
        rollback = [r for r in cluster.trace if r.get("event") == "rollback"]
        self.assertEqual(len(rollback), 1)
        self.assertEqual((rollback[0]["node"], rollback[0]["fork"]), ("n1", 1))

    def test_slow_clock_primary_stops_writing_before_replacement(self):
        # n1's clock is 20% slow.  It must stop accepting writes before the
        # controller promotes n2, or two primaries would accept concurrently.
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 10, "drift": 0.2,
                "clock_rate": {"n1": 0.8}, "ticks": 20,
                "events": [{"t": 3, "partition": [["n1"], ["n2", "n3", "ctl"]]},
                           {"t": 16, "heal": True}],
                "writes": [{"t": t, "node": "n1", "key": "x", "value": f"n1-{t}"}
                           for t in range(9, 15)] +
                          [{"t": t, "node": "n2", "key": "y", "value": f"n2-{t}"}
                           for t in range(12, 15)]}
        cluster, report = run_cluster(spec)
        self.assertTrue(report["single_writer"], report)
        self.assertTrue(report["consistent"], report)
        by_tick = {}
        for row in cluster.trace:
            if "key" in row and row["result"] == "ok":
                by_tick.setdefault(row["t"], set()).add(row["node"])
        self.assertTrue(all(len(nodes) == 1 for nodes in by_tick.values()), by_tick)
        self.assertEqual([r["node"] for r in cluster.trace if r.get("event") == "promote"], ["n2"])

    def test_partition_without_majority_never_elects(self):
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 3, "ticks": 10,
                "events": [{"t": 2, "partition": [["n1"], ["n2"], ["n3"]]}]}
        cluster, report = run_cluster(spec)
        self.assertEqual([r for r in cluster.trace if r.get("event") == "promote"], [])
        self.assertEqual(report["primary"], "n1")  # stays primary, lease expires
        self.assertFalse(cluster.node("n1").writable(10, 3, 0.0))

    def test_scenario_validation_rejects_unsafe_clock(self):
        with self.assertRaises(ScenarioError):
            validate({"nodes": ["n1"], "lease": 1, "drift": 0.1, "ticks": 1,
                      "clock_rate": {"n1": 0.5}})


class CliTests(unittest.TestCase):
    def _run(self, spec):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "s.json")
        with open(path, "w") as fh:
            json.dump(spec, fh)
        out = os.path.join(d, "out")
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "ha.py"), "run", path, out],
                              check=True, capture_output=True, text=True)
        return out, proc

    def test_lagging_replica_catches_up(self):
        leader, replica = Node("n1"), Node("n2")
        leader.log = [{"term": 1, "key": "a", "value": "1"}, {"term": 1, "key": "b", "value": "2"}]
        replica.log = leader.log[:1]
        self.assertTrue(replica.receive(leader))
        self.assertEqual(replica.kv(), {"a": "1", "b": "2"})
        self.assertEqual(replica.conflicts, [])

    def test_steady_cluster(self):
        spec = {"nodes": ["n1", "n2", "n3"], "lease": 5, "ticks": 6,
                "writes": [{"t": t, "node": "n1", "key": "k", "value": str(t)} for t in range(1, 5)]}
        out, _ = self._run(spec)
        with open(os.path.join(out, "state.json")) as fh:
            state = json.load(fh)
        self.assertEqual({n: s["kv"] for n, s in state.items()},
                         {n: {"k": "4"} for n in ("n1", "n2", "n3")})

    def test_run_writes_all_conflict_files_including_empty(self):
        spec = {"nodes": ["n1", "n2"], "lease": 5, "ticks": 2,
                "writes": [{"t": 1, "node": "n1", "key": "a", "value": "1"}]}
        out, proc = self._run(spec)
        for name in ("n1", "n2"):
            self.assertTrue(os.path.exists(os.path.join(out, "conflicts", f"{name}.jsonl")))
        trace_path = os.path.join(out, "trace.jsonl")
        with open(trace_path) as fh:
            trace = [json.loads(line) for line in fh]
        self.assertEqual(trace[0]["result"], "ok")
        self.assertEqual(proc.stdout.strip(), "")


class ExampleTests(unittest.TestCase):
    def test_all_examples_pass_verify(self):
        example_dir = os.path.join(ROOT, "examples")
        files = sorted(f for f in os.listdir(example_dir) if f.endswith(".json"))
        self.assertGreaterEqual(len(files), 3)
        for name in files:
            proc = subprocess.run(
                [sys.executable, os.path.join(ROOT, "ha.py"), "verify",
                 os.path.join(example_dir, name)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, (name, proc.stdout, proc.stderr))
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(report["single_writer"], name)
            self.assertTrue(report["consistent"], name)
            self.assertEqual(report["violations"], [], name)


class FuzzSafetyTests(unittest.TestCase):
    """Randomised partitions, crashes, healings and clock rates.

    The single-writer and convergence properties must hold for every seed;
    a failure here means the fencing argument has a hole.
    """

    def test_random_schedules_keep_invariants(self):
        import random
        for seed in range(300):
            rng = random.Random(seed)
            names = ["n1", "n2", "n3"]
            ticks = 40
            drift = rng.choice([0.0, 0.05, 0.2])
            # Pin clocks at the drift extremes half the time: a rate outside
            # [1 - drift, 1 + drift] would break the lease margin, so exactly
            # on the bound is the adversarial case that must still be safe.
            if rng.random() < 0.5:
                rates = {n: drift_edge(drift, rng) for n in names}
            else:
                rates = {n: round(rng.uniform(1 - drift, 1 + drift), 3) for n in names}
            events, writes = [], []
            for t in range(1, ticks + 1):
                roll = rng.random()
                if roll < 0.12:
                    # Random 1-3 way partition of the cluster plus controller.
                    groups, pool = [], names + ["ctl"]
                    rng.shuffle(pool)
                    cuts = sorted(rng.sample(range(1, len(pool)), rng.randint(1, 2)))
                    prev = 0
                    for cut in cuts + [len(pool)]:
                        groups.append(pool[prev:cut])
                        prev = cut
                    events.append({"t": t, "partition": groups})
                elif roll < 0.20:
                    events.append({"t": t, "heal": True})
                elif roll < 0.28:
                    target = rng.choice(names)
                    events.append({"t": t, "crash": target})
                    if rng.random() < 0.7:
                        restart = min(ticks, t + rng.randint(1, 8))
                        events.append({"t": restart, "restart": target})
                for _ in range(rng.randint(0, 2)):
                    writes.append({"t": t, "node": rng.choice(names),
                                   "key": rng.choice(["a", "b", "c"]),
                                   "value": f"{seed}-{t}-{rng.random():.4f}"})
            # Convergence is eventual: a partition still in force on the last
            # tick legitimately leaves logs unmerged, so heal and go quiet.
            events = [e for e in events if e["t"] <= ticks - 4 and not e.get("heal")]
            events.append({"t": ticks - 3, "heal": True})
            events = [e for e in events if not (e.get("crash") and e["t"] > ticks - 3)]
            for target in names:
                if not any(e.get("restart") == target and e["t"] >= ticks - 3 for e in events):
                    events.append({"t": ticks - 3, "restart": target})
            writes = [w for w in writes if w["t"] <= ticks - 3]
            spec = {"nodes": names, "lease": rng.choice([2, 3, 5]), "drift": drift,
                    "clock_rate": rates, "ticks": ticks, "events": events, "writes": writes}
            cluster, report = run_cluster(spec)
            self.assertTrue(report["single_writer"], (seed, report))
            self.assertEqual(report["violations"], [], seed)
            self.assertTrue(report["consistent"], (seed, report))
            self.assertIsNotNone(report["primary"], seed)


if __name__ == "__main__":
    unittest.main()
