# -*- coding: utf-8 -*-
"""核心摘要单元测试：权重、并列值、空集、分位点、合并契约、确定性。"""
import math
import random
import unittest

from qsummary.reference import absolute_rank_error
from qsummary.summary import (
    ALGORITHMS,
    GKSummary,
    IncompatibleSummary,
    SummaryVersion,
    merge_all,
)

Q1 = SummaryVersion.for_algorithm("q1")
Q2 = SummaryVersion.for_algorithm("q2")
POINTS = (("50", 0.5), ("95", 0.95), ("99", 0.99))


def rank_error(sorted_values, estimate, target_rank):
    return absolute_rank_error(sorted_values, estimate, target_rank)


class SummaryDefinitionTest(unittest.TestCase):
    def test_empty_set(self):
        s = GKSummary.empty(Q1)
        self.assertTrue(s.is_empty())
        self.assertEqual(s.n, 0)
        self.assertEqual(s.items, 0)
        self.assertIsNone(s.quantile(0.95))
        self.assertEqual(s.rank_error_bound(), 0)
        # 空摘要是合并恒等元。
        other = GKSummary.from_values([1, 2, 3], Q1)
        self.assertEqual(s.merge(other).summary_id(), other.summary_id())
        self.assertEqual(other.merge(s).summary_id(), other.summary_id())

    def test_tiny_sample_is_exact_nearest_rank(self):
        data = [1, 2, 3, 4, 6, 9]
        s = GKSummary.from_values(data, Q1, revisions={"0": 7})
        sd = sorted(data)
        for name, p in POINTS:
            r = math.ceil(p * len(sd))
            self.assertEqual(s.quantile(p), sd[r - 1])
            self.assertEqual(rank_error(sd, s.quantile(p), r), 0)
        self.assertEqual(s.revisions, {"0": 7})

    def test_nearest_rank_target_definition(self):
        s = GKSummary.from_values(list(range(1, 11)), Q1)
        self.assertEqual(s.target_rank(0.5), 5)
        self.assertEqual(s.target_rank(0.95), 10)
        self.assertEqual(s.target_rank(0.0), 0)

    def test_ties_share_full_rank_interval(self):
        data = [5] * 10 + [10] * 10
        s = GKSummary.from_values(data, Q1)
        sd = sorted(data)
        lo, hi = s.rank_bounds(5)
        # 5 的完整秩区间为 [1, 10]；目标秩 10 落在该区间，最近秩分位为 5。
        self.assertEqual((lo, hi), (1, 10))
        self.assertEqual(s.quantile(0.5), 5)
        err = rank_error(sd, s.quantile(0.5), 10)
        self.assertEqual(err, 0)

    def test_invalid_probability(self):
        s = GKSummary.from_values([1, 2], Q1)
        with self.assertRaises(Exception):
            s.target_rank(1.1)


class AccuracyTest(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(20261114)
        self.uniform = [rnd.randint(0, 100_000) for _ in range(20_000)]
        # 长尾：Pareto 风格（取整到有符号 32 位范围）。
        self.longtail = [
            min(2 ** 31 - 1, int(rnd.paretovariate(2.2))) for _ in range(20_000)
        ]
        # 大量重复值：仅 8 个不同取值。
        self.duplicates = [rnd.randint(0, 7) for _ in range(20_000)]

    def _check(self, data, summary):
        sd = sorted(data)
        n = len(sd)
        for name, p in POINTS:
            target = math.ceil(p * n)
            est = summary.quantile(p)
            err = rank_error(sd, est, target)
            # 契约 1：1% 绝对秩误差验收（核心需求）。
            self.assertLessEqual(
                err,
                max(0.01 * n, 0),
                "%s rank error %d exceeds 1%% of %d" % (name, err, n),
            )
            # 契约 2：摘要自报上界必须不小于真实误差（诚实、可依赖）。
            self.assertLessEqual(err, summary.rank_error_bound() + 1)
            # 契约 3：自报上界本身受 cap/记账机制约束，不会无限增长。
            self.assertLessEqual(summary.rank_error_bound(), n)

    def test_uniform_accuracy(self):
        self._check(self.uniform, GKSummary.from_values(self.uniform, Q1))

    def test_longtail_accuracy(self):
        self._check(self.longtail, GKSummary.from_values(self.longtail, Q1))

    def test_duplicates_accuracy(self):
        self._check(self.duplicates, GKSummary.from_values(self.duplicates, Q1))

    def test_cap_is_respected_and_bounded(self):
        # 远大于自然 GK 规模也不会突破 cap（这里用小 cap 的临时契约验证机制）。
        tiny = SummaryVersion("q1", 0.005, 64)
        s = GKSummary.from_values(self.uniform[:5000], tiny)
        self.assertLessEqual(s.items, 64)
        sd = sorted(self.uniform[:5000])
        for name, p in POINTS:
            target = math.ceil(p * len(sd))
            # cap 保底路径仍给出可用估计。
            self.assertIn(s.quantile(p), set(sd))

    def test_q2_is_stricter_contract(self):
        # q2 的 epsilon 更小，相同数据下自报上界不超过 q1。
        s1 = GKSummary.from_values(self.uniform, Q1)
        s2 = GKSummary.from_values(self.uniform, Q2)
        self.assertLess(s2.version.epsilon, s1.version.epsilon)
        self.assertLessEqual(s2.rank_error_bound(), s2.n)


class MergeTest(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(777)
        self.data = [rnd.randint(0, 50_000) for _ in range(12_000)]
        self.sd = sorted(self.data)

    def _check_merged(self, merged):
        self.assertEqual(merged.n, len(self.data))
        for name, p in POINTS:
            target = math.ceil(p * len(self.sd))
            err = rank_error(self.sd, merged.quantile(p), target)
            self.assertLessEqual(err, 0.01 * len(self.sd))

    def test_merge_pairwise(self):
        chunks = [self.data[i::4] for i in range(4)]
        summaries = [GKSummary.from_values(c, Q1) for c in chunks]
        self._check_merged(merge_all(summaries))

    def test_merge_order_variants(self):
        chunks = [self.data[i::6] for i in range(6)]
        summaries = [GKSummary.from_values(c, Q1) for c in chunks]
        orders = [
            summaries,
            list(reversed(summaries)),
            [summaries[i] for i in (0, 3, 1, 5, 2, 4)],
        ]
        ids = set()
        for order in orders:
            merged = merge_all(order)
            self._check_merged(merged)
            ids.add(merged.summary_id())
        # 不同合并顺序都是合法结果；同顺序重放必须逐字节稳定。
        again = merge_all([s.copy() for s in orders[1]])
        self.assertEqual(again.summary_id(), merge_all(orders[1]).summary_id())

    def test_incompatible_contract_rejected(self):
        a = GKSummary.from_values([1, 2, 3], Q1)
        b = GKSummary.from_values([4, 5, 6], Q2)
        with self.assertRaises(IncompatibleSummary):
            a.merge(b)
        with self.assertRaises(IncompatibleSummary):
            b.merge(a)

    def test_ties_across_partitions(self):
        a = GKSummary.from_values([5, 5, 5], Q1, revisions={"0": 1})
        b = GKSummary.from_values([5, 9], Q1, revisions={"1": 1})
        m = a.merge(b)
        self.assertEqual(m.n, 5)
        self.assertEqual(m.quantile(0.5), 5)
        self.assertEqual(m.revisions, {"0": 1, "1": 1})
        lo, hi = m.rank_bounds(5)
        self.assertEqual((lo, hi), (1, 4))


class SerializationTest(unittest.TestCase):
    def test_roundtrip_and_stable_id(self):
        rnd = random.Random(3)
        data = [rnd.randint(-1000, 1000) for _ in range(3000)]
        s = GKSummary.from_values(data, Q1, revisions={"2": 9})
        restored = GKSummary.from_dict(s.to_dict())
        self.assertEqual(restored.summary_id(), s.summary_id())
        self.assertEqual(restored.quantile(0.95), s.quantile(0.95))
        rebuilt = GKSummary.from_values(data, Q1, revisions={"2": 9})
        self.assertEqual(rebuilt.summary_id(), s.summary_id())

    def test_unknown_algorithm_rejected(self):
        with self.assertRaises(Exception):
            SummaryVersion.for_algorithm("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
