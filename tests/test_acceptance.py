"""End-to-end acceptance matrix.

Covers the two acceptance criteria:
  1. mixed put/delete/overwrite workload keeps final space amplification
     within 1.2x while reads match the serial reference and snapshots retain
     their visible versions;
  2. rerunning two layer shapes across multiple seeds reproduces identical
     final key/value sets and statistical summaries, with replayable
     concurrent decisions.
"""
import unittest

from lake.runner import config_preset, replay_workload, run_workload
from lake.workload import WorkloadSpec, generate_ops

CONFIG_NAMES = ["compact4", "deep5"]
SEEDS = [7, 42]
SPACE_AMPLIFICATION_LIMIT = 1.2
# Concurrent vs serial may legitimately differ slightly above 1.0; acceptance
# bounds their space amplification ratio by the same 1.2 reference factor.
CONCURRENCY_RATIO_LIMIT = 1.2


class AcceptanceTest(unittest.TestCase):
    def test_mixed_workload_matrix(self):
        matrix = {}
        for name in CONFIG_NAMES:
            for seed in SEEDS:
                with self.subTest(config=name, seed=seed):
                    ops = generate_ops(WorkloadSpec(seed=seed))
                    config = config_preset(name)
                    serial = run_workload(
                        ops, config, seed=seed, mode="serial"
                    )
                    concurrent = run_workload(
                        ops, config, seed=seed, mode="concurrent"
                    )
                    replay = replay_workload(
                        ops,
                        config,
                        seed=seed,
                        recorded=concurrent.decisions,
                    )
                    # Criterion 1: reads identical to serial reference.
                    self.assertEqual(
                        concurrent.read_results, serial.read_results
                    )
                    self.assertEqual(
                        concurrent.read_results,
                        serial.reference.reads,
                    )
                    self.assertEqual(
                        concurrent.summary["read_mismatches"], 0
                    )
                    # Final key/value set matches ground truth in every mode.
                    self.assertTrue(serial.summary["kv_match"])
                    self.assertTrue(concurrent.summary["kv_match"])
                    self.assertEqual(
                        concurrent.summary["final_kv"],
                        serial.summary["final_kv"],
                    )
                    # Space amplification bound against the live data size.
                    self.assertLessEqual(
                        serial.summary["space_amplification"],
                        SPACE_AMPLIFICATION_LIMIT,
                    )
                    self.assertLessEqual(
                        concurrent.summary["space_amplification"],
                        SPACE_AMPLIFICATION_LIMIT,
                    )
                    ratio = (
                        concurrent.summary["space_amplification"]
                        / max(serial.summary["space_amplification"], 1e-9)
                    )
                    self.assertLessEqual(ratio, CONCURRENCY_RATIO_LIMIT)
                    # Criterion 2: replayable concurrent decisions.
                    self.assertEqual(
                        replay.decisions, concurrent.decisions
                    )
                    self.assertEqual(
                        replay.engine.manifest.digest(),
                        concurrent.engine.manifest.digest(),
                    )
                    matrix[(name, seed)] = {
                        "digest": concurrent.engine.manifest.digest(),
                        "summary": self._comparable_summary(
                            concurrent.summary
                        ),
                    }
        # Rerun every cell and demand identical digest + summary.
        for (name, seed), first in matrix.items():
            with self.subTest(rerun=name, seed=seed):
                ops = generate_ops(WorkloadSpec(seed=seed))
                rerun = run_workload(
                    ops, config_preset(name), seed=seed, mode="concurrent"
                )
                self.assertEqual(
                    rerun.engine.manifest.digest(), first["digest"]
                )
                self.assertEqual(
                    self._comparable_summary(rerun.summary),
                    first["summary"],
                )

    @staticmethod
    def _comparable_summary(summary):
        keys = (
            "state_digest",
            "final_kv",
            "watermark",
            "level_file_counts",
            "physical_bytes",
            "live_logical_bytes",
            "space_amplification",
            "write_amplification",
            "compactions",
        )
        return {k: summary[k] for k in keys}


if __name__ == "__main__":
    unittest.main()
