"""Concurrent scheduler, decision-log replay and abort-path tests."""
import unittest

from lake.engine import CompactionConflict, Engine
from lake.model import Config
from lake.planner import Planner
from lake.runner import replay_workload, run_workload
from lake.scheduler import Scheduler
from lake.workload import WorkloadSpec, generate_ops


def cfg():
    return Config(
        levels=4,
        file_caps=(3, 3, 6, 12),
        memtable_records=3,
        target_file_records=8,
        max_concurrent_compactions=2,
    )


class SchedulerTest(unittest.TestCase):
    def test_seeded_runs_are_identical(self):
        ops = generate_ops(WorkloadSpec(seed=7))
        r1 = run_workload(ops, cfg(), seed=99, mode="concurrent")
        r2 = run_workload(ops, cfg(), seed=99, mode="concurrent")
        self.assertEqual(r1.decisions, r2.decisions)
        self.assertEqual(
            r1.engine.manifest.digest(), r2.engine.manifest.digest()
        )

    def test_different_seeds_can_diverge_but_converge_same_kv(self):
        ops = generate_ops(WorkloadSpec(seed=7))
        r1 = run_workload(ops, cfg(), seed=1, mode="concurrent")
        r2 = run_workload(ops, cfg(), seed=2, mode="concurrent")
        self.assertEqual(
            r1.summary["final_kv"], r2.summary["final_kv"]
        )

    def test_decision_log_replays_state_bit_for_bit(self):
        ops = generate_ops(WorkloadSpec(seed=7))
        original = run_workload(ops, cfg(), seed=11, mode="concurrent")
        replayed = replay_workload(
            ops, cfg(), seed=11, recorded=original.decisions
        )
        self.assertEqual(replayed.decisions, original.decisions)
        self.assertEqual(
            replayed.engine.manifest.digest(),
            original.engine.manifest.digest(),
        )
        self.assertEqual(replayed.read_results, original.read_results)

    def test_strict_version_mode_aborts_stale_plan(self):
        e = Engine.create(cfg())
        scheduler = Scheduler(e, seed=3, strict_version=True)
        for i in range(8):
            e.put("k%02d" % i, i)
        planner = Planner(e.manifest)
        plan = planner.pick()
        self.assertIsNotNone(plan)
        # Mutate the manifest so the planned state_version is stale, then ask
        # the strict coordinator to commit it.
        e.manifest.bump()
        with self.assertRaises(CompactionConflict):
            e.execute(plan, strict_version=True)

    def test_optimistic_mode_commits_when_inputs_still_valid(self):
        e = Engine.create(cfg())
        for i in range(8):
            e.put("k%02d" % i, i)
        plan = Planner(e.manifest).pick()
        before = e.manifest.state_version
        e.manifest.bump()  # unrelated structural tick
        report = e.execute(plan, strict_version=False)
        self.assertIn("output_files", report)
        self.assertGreater(e.manifest.state_version, before)

    def test_no_overlapping_inputs_within_a_pump_round(self):
        ops = generate_ops(WorkloadSpec(seed=7))
        result = run_workload(ops, cfg(), seed=5, mode="concurrent")
        per_pump = {}
        for d in result.decisions:
            if d["kind"] == "plan":
                per_pump.setdefault(d["pump"], []).append(
                    set(d["plan"]["input_files"])
                )
        for pump_id, groups in per_pump.items():
            for a_i in range(len(groups)):
                for b_i in range(a_i + 1, len(groups)):
                    self.assertEqual(
                        groups[a_i] & groups[b_i],
                        set(),
                        "overlapping plans in pump %d" % pump_id,
                    )

    def test_long_lived_snapshot_keeps_versions_under_concurrency(self):
        # Handcrafted sequence: open a snapshot, then overwrite/delete the same
        # keys repeatedly while concurrent compactions run.  Every snapshot
        # read must return the value visible at snapshot creation time.
        from lake.workload import ReferenceDB

        ops = [
            {"op": "put", "k": "k1", "v": "orig"},
            {"op": "put", "k": "k2", "v": "keep"},
            {"op": "snap", "snap": 1},
        ]
        for i in range(30):
            ops.append({"op": "put", "k": "k1", "v": i})
            if i % 3 == 0:
                ops.append({"op": "del", "k": "k2"})
                ops.append({"op": "put", "k": "k2", "v": "rebirth%d" % i})
            ops.append({"op": "read", "k": "k1", "snap": 1})
            ops.append({"op": "read", "k": "k2", "snap": 1})
        ops.append({"op": "release", "snap": 1})
        result = run_workload(ops, cfg(), seed=13, mode="concurrent")
        ref = ReferenceDB()
        for op in ops:
            ref.apply(op)
        # Snapshot reads: k1 always 'orig', k2 always 'keep' at snap 1.
        snap_reads = [
            result.read_results[i]
            for i, ob in enumerate(ref.read_observations)
            if ob["snap"] == 1
        ]
        self.assertTrue(snap_reads)
        for value in snap_reads[0::2]:
            self.assertEqual(value, "orig")
        for value in snap_reads[1::2]:
            self.assertEqual(value, "keep")
        # Current view still matches ground truth after the snapshot closes.
        self.assertEqual(result.summary["read_mismatches"], 0)
        self.assertTrue(result.summary["kv_match"])


if __name__ == "__main__":
    unittest.main()
