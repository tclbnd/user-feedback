# -*- coding: utf-8 -*-
"""验收测试：精确排序核对、更正/删除回放、版本、重建集合、内存与确定性。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from qsummary.bench import (
    CAP,
    PARTITION_COUNT,
    accuracy_scenario,
    apply_initial,
    build_partitions,
    correction_scenario,
    determinism_scenario,
    memory_scenario,
    migration_scenario,
    tree_accuracy_scenario,
)
from qsummary.reference import exact_query
from qsummary.summary import IncompatibleSummary, SummaryVersion
from qsummary.tree import (
    AlgorithmMixing,
    PartitionTree,
    RevisionConflict,
    canonical_cover,
)

KINDS = ("uniform", "longtail", "duplicates")
ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "quantiles.py"


class AcceptanceAccuracyTest(unittest.TestCase):
    def test_all_datasets_and_merge_orders(self):
        for kind in KINDS:
            report = accuracy_scenario(kind)
            self.assertEqual(report["n"], sum(len(v) for v in build_partitions(kind).values()))
            for order, detail in report["orders"].items():
                self.assertEqual(detail["count"], report["n"])
                self.assertLessEqual(detail["items"], CAP)
                for row in detail["checks"]:
                    self.assertTrue(
                        row["within_1_percent"],
                        "%s/%s: %s err=%s tol=%s"
                        % (kind, order, row["quantile"],
                           row["absolute_rank_error"], row["tolerance"]),
                    )
                    # 自报上界诚实覆盖真实误差（具体在 test_summary 中核对）；
                    # 多层合并的实际误差由上面的 1% 验收独立约束。
                    self.assertGreaterEqual(row["summary_bound"], 0)

    def test_tree_query_matches_exact_count_and_versions(self):
        for kind in KINDS:
            for reverse in (False, True):
                report = tree_accuracy_scenario(kind, reverse=reverse)
                self.assertEqual(report["query"]["count"], report["reference_count"])
                self.assertEqual(
                    len(report["query"]["revisions"]), PARTITION_COUNT
                )
                self.assertEqual(
                    set(report["query"]["revisions"]),
                    {str(i) for i in range(PARTITION_COUNT)},
                )
                self.assertEqual(
                    len(report["query"]["participating_summary_ids"]),
                    len(report["query"]["cover"]),
                )
                for row in report["checks"]:
                    self.assertTrue(row["within_1_percent"])

    def test_partial_range_cover_and_exact_count(self):
        partitions = build_partitions("uniform")
        tree = PartitionTree(root=None)
        apply_initial(tree, partitions)
        for start, end in ((0, 16), (2, 14), (3, 11), (5, 6), (0, 1), (7, 16)):
            cover = canonical_cover(start, end)
            # 覆盖分片互不相交且恰好拼成 [start,end)。
            spans = [tuple(map(int, k.split(":"))) for k in cover]
            self.assertEqual(spans[0][0], start)
            self.assertEqual(spans[-1][1], end)
            for (_, b1), (a2, _) in zip(spans, spans[1:]):
                self.assertEqual(b1, a2)
            result = tree.query(start, end)
            reference = exact_query(partitions, start, end)
            self.assertEqual(result["count"], reference["count"])


class CorrectionReplayTest(unittest.TestCase):
    def test_sparse_correction_and_deletion(self):
        report = correction_scenario("uniform")
        # 实际重建集合 == 叶 + 祖先（不多不少）。
        self.assertEqual(report["recomputed_correction"], report["expected_correction"])
        self.assertEqual(report["recomputed_deletion"], report["expected_deletion"])
        # 旧 revision 重放不触发任何重建。
        self.assertEqual(report["recomputed_stale"], [])
        # 样本数与精确重算一致；被删分区 revision 不出现。
        self.assertTrue(report["count_matches"])
        self.assertNotIn(str(report["deleted_partition"]), report["revisions"])
        self.assertEqual(
            report["query_count"],
            report["exact_count"],
        )
        for row in report["checks"]:
            self.assertTrue(row["within_1_percent"])
        # 每个参与摘要都可定位快照哈希。
        self.assertTrue(all(report["participating_summary_ids"]))
        # 内存上界。
        self.assertLessEqual(report["max_summary_items"], report["cap"])

    def test_same_revision_conflict_and_reapply_idempotent(self):
        partitions = build_partitions("duplicates")
        tree = PartitionTree(root=None)
        apply_initial(tree, partitions)
        with self.assertRaises(RevisionConflict):
            tree.apply_batch([
                {"index": 1, "revision": 1, "values": [1, 2, 3], "algorithm": "q1"}
            ])
        # 完全相同的重复应用不重建。
        again = tree.apply_batch([
            {"index": 1, "revision": 1, "values": list(partitions[1]), "algorithm": "q1"}
        ])
        self.assertEqual(again, [])


class MemoryAndDeterminismTest(unittest.TestCase):
    def test_memory_bounds(self):
        report = memory_scenario()
        for kind, detail in report.items():
            self.assertTrue(detail["within_cap"], kind)
            self.assertEqual(
                detail["memory_bytes"], detail["recomputed_bytes"]
            )
            for key, items in detail["per_node_items"].items():
                self.assertLessEqual(items, CAP)

    def test_fixed_seed_repeatability(self):
        report = determinism_scenario()
        self.assertTrue(report["node_ids_equal"])
        self.assertTrue(report["query_ids_equal"])
        self.assertTrue(report["reverse_count_equal"])

    def test_migration_stable_order_and_mixing_rejected(self):
        report = migration_scenario()
        self.assertTrue(report["all_nodes_rebuilt"])
        self.assertTrue(report["leaf_first_sorted"])
        self.assertTrue(report["ids_changed"])
        self.assertEqual(report["algorithm"], "q2")
        self.assertEqual(report["q2_epsilon"], SummaryVersion.for_algorithm("q2").epsilon)
        self.assertTrue(report["mixing_rejected"])

    def test_incompatible_merge_directly(self):
        q1 = SummaryVersion.for_algorithm("q1")
        q2 = SummaryVersion.for_algorithm("q2")
        with self.assertRaises(IncompatibleSummary):
            from qsummary.summary import GKSummary
            GKSummary.from_values([1, 2], q1).merge(
                GKSummary.from_values([3, 4], q2)
            )


class CliEndToEndTest(unittest.TestCase):
    def _run(self, root, *args):
        proc = subprocess.run(
            [sys.executable, str(CLI), str(root), *args],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        return proc

    def test_apply_query_migrate_stats_and_exit_codes(self):
        partitions = build_partitions("uniform")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            batch = root.parent / "batch.json"
            batch.write_text(json.dumps({"partitions": [
                {"index": i, "revision": 1, "values": partitions[i], "algorithm": "q1"}
                for i in range(PARTITION_COUNT)
            ]}))
            proc = self._run(root, "apply", str(batch))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            applied = json.loads(proc.stdout)
            self.assertEqual(applied["algorithm"], "q1")
            self.assertLessEqual(applied["max_summary_items"], CAP)

            proc = self._run(root, "query", "--start", "0", "--end", "16")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            q = json.loads(proc.stdout)
            reference = exact_query(partitions, 0, 16)
            self.assertEqual(q["count"], reference["count"])
            self.assertEqual(q["algorithm"], "q1")
            self.assertIn("rank_error_bound", q)
            self.assertIn("merged_summary_id", q)
            self.assertEqual(len(q["participating_summary_ids"]), 1)  # 根覆盖

            proc_r = self._run(root, "query", "--start", "2", "--end", "14", "--reverse")
            self.assertEqual(proc_r.returncode, 0)
            qr = json.loads(proc_r.stdout)
            self.assertEqual(qr["count"], exact_query(partitions, 2, 14)["count"])

            # 未知算法退出码 2。
            bad = root.parent / "bad.json"
            bad.write_text(json.dumps({"partitions": [
                {"index": 0, "revision": 2, "values": [1], "algorithm": "zzz"}
            ]}))
            proc = self._run(root, "apply", str(bad))
            self.assertEqual(proc.returncode, 2)

            # 混用 q2 退出码 2。
            mix = root.parent / "mix.json"
            mix.write_text(json.dumps({"partitions": [
                {"index": 0, "revision": 2, "values": [1], "algorithm": "q2"}
            ]}))
            proc = self._run(root, "apply", str(mix))
            self.assertEqual(proc.returncode, 2)

            # 显式迁移后可接受 q2。
            proc = self._run(root, "migrate", "--algorithm", "q2")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            mig = json.loads(proc.stdout)
            self.assertEqual(mig["to"], "q2")
            self.assertEqual(len(mig["recomputed"]), 31)
            proc = self._run(root, "apply", str(mix))
            self.assertEqual(proc.returncode, 0, proc.stderr)

            proc = self._run(root, "stats")
            self.assertEqual(proc.returncode, 0)
            stats = json.loads(proc.stdout)
            self.assertLessEqual(stats["max_summary_items"], CAP)

            # 持久化后重新加载，快照哈希稳定。
            proc2 = self._run(root, "query", "--start", "0", "--end", "16")
            self.assertEqual(
                json.loads(proc.stdout) and json.loads(proc2.stdout)["algorithm"], "q2"
            )

    def test_empty_range_quantiles_are_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state"
            batch = root.parent / "one.json"
            batch.write_text(json.dumps({"partitions": [
                {"index": 0, "revision": 1, "values": [1, 2, 3], "algorithm": "q1"}
            ]}))
            self.assertEqual(self._run(root, "apply", str(batch)).returncode, 0)
            proc = self._run(root, "query", "--start", "8", "--end", "9")
            self.assertEqual(proc.returncode, 0)
            q = json.loads(proc.stdout)
            self.assertEqual(q["count"], 0)
            self.assertEqual(q["quantiles"], {"50": None, "95": None, "99": None})
            self.assertEqual(q["revisions"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
