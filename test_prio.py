#!/usr/bin/env python3
"""prio.py 单元测试 + 独立参考实现逐条核对 (Python3.9+ stdlib, python3 test_prio.py)。"""
import copy
import json
import os
import random
import unittest

import prio
import simulator

DAY = 86400
NOW = 1_700_000_000


def reference_score(page, etag_now, now, horizon):
    """独立重写 README 参考公式, 与 prio.py 不共享任何实现。"""
    if page.get("lastmod") is None:
        fresh = 0.5
    else:
        age = min(max(0.0, (now - page["lastmod"]) / DAY), 30.0) / 7.0
        fresh = 1.0 / (1.0 + age)
    old, new = page.get("etag"), etag_now.get(page["url"])
    etag = 0.3 if (old and new and old != new) else 0.0
    hist = page.get("hist")
    hs = min(1.0, max(0.0, hist) * horizon / 7.0) if hist is not None else 0.0
    return 0.4 * page.get("imp", 0.0) + 0.2 * fresh + 0.2 * etag + 0.2 * hs


class SignalSemanticsTests(unittest.TestCase):
    def test_freshness(self):
        f, miss, stale = prio.freshness_signal(None, NOW, 7)
        self.assertEqual((f, miss, stale), (0.5, True, False))
        f, miss, stale = prio.freshness_signal(NOW, NOW, 7)
        self.assertAlmostEqual(f, 1.0)
        self.assertFalse(miss)
        f, _, stale = prio.freshness_signal(NOW - 7 * DAY, NOW, 7)
        self.assertAlmostEqual(f, 0.5)
        self.assertFalse(stale)                      # 等于 horizon 不算过期
        _, _, stale = prio.freshness_signal(NOW - 8 * DAY, NOW, 7)
        self.assertTrue(stale)                       # 超过 horizon 才标记过期
        f1, _, _ = prio.freshness_signal(NOW - 30 * DAY, NOW, 7)
        f2, _, _ = prio.freshness_signal(NOW - 90 * DAY, NOW, 7)
        self.assertAlmostEqual(f1, f2)               # 30 天封顶, 更旧不再下降

    def test_etag(self):
        self.assertEqual(prio.etag_signal("a", "b"), (0.3, True, False))
        self.assertEqual(prio.etag_signal("a", "a"), (0.0, False, False))
        self.assertEqual(prio.etag_signal(None, "b"), (0.0, False, True))
        self.assertEqual(prio.etag_signal("a", None), (0.0, False, True))
        self.assertEqual(prio.etag_signal("", ""), (0.0, False, True))

    def test_hist(self):
        self.assertEqual(prio.hist_signal(None, 7), 0.0)
        self.assertAlmostEqual(prio.hist_signal(2, 7), 1.0)          # 截断到 1
        self.assertAlmostEqual(prio.hist_signal(0.5, 14), 1.0)       # horizon 折算
        self.assertAlmostEqual(prio.hist_signal(0.5, 7), 0.5)


class ReferenceEquivalenceTests(unittest.TestCase):
    def test_item_by_item(self):
        rnd = random.Random(42)
        etag_now = {}
        pages = []
        for i in range(200):
            u = "https://x/p%03d" % i
            p = {"url": u, "imp": round(rnd.random(), 4)}
            if rnd.random() < 0.8:
                p["lastmod"] = NOW - rnd.randint(0, 45) * DAY
            if rnd.random() < 0.8:
                p["etag"] = "old%d" % i
            if rnd.random() < 0.8:
                p["hist"] = round(rnd.uniform(0, 3), 3)
            if rnd.random() < 0.85:
                etag_now[u] = ("new%d" % i) if rnd.random() < 0.5 else p.get("etag", "x")
            pages.append(p)
        merged = prio.merge_pages(pages)
        for rec in merged:
            got = prio.score_page(rec, etag_now, NOW, 7)["p"]
            ref = reference_score(rec, etag_now, NOW, 7)
            self.assertAlmostEqual(got, ref, places=12,
                                   msg="优先级与参考实现不一致: " + rec["url"])

    def test_two_cycles_match_reference(self):
        # 双周期模拟器输出的每条决策都与参考公式逐条一致
        clock = simulator.VirtualClock(simulator.T0)
        state, rnd = simulator.build_site()
        high = sorted(u for u in state if state[u]["imp"] >= 0.70)
        mid = sorted(u for u in state if 0.40 <= state[u]["imp"] < 0.70)
        low = sorted(u for u in state if state[u]["imp"] < 0.40)
        changed = high[:4] + mid[:3] + low[:2]
        change_log = {}
        for u in changed:
            simulator.inject_change(state, u, clock.now, "t", change_log)
        for u, imp in [("https://site/new-a", 0.9)]:
            simulator.discover(state, u, imp)
        feed = simulator.build_feed(state, clock.now, 7, change_log)
        out = prio.run(feed)
        by_url = {d["url"]: d for d in out["decisions"]}
        merged = {m["url"]: m for m in prio.merge_pages(feed["pages"])}
        for u, d in by_url.items():
            if d["signals"]["fast_lane"]:
                continue                                # 快速通道页面 etag_now 无记录
            ref = reference_score(merged[u], feed["etag_now"], clock.now, 7)
            self.assertAlmostEqual(d["p"], round(ref, 6), places=12)


class MergeAndOrderTests(unittest.TestCase):
    def _base(self):
        return {
            "now": NOW, "budget": 3, "horizon": 7,
            "pages": [
                {"url": "u1", "imp": 0.9, "lastmod": NOW - DAY, "etag": "e1", "hist": 1.0},
                {"url": "u1", "imp": 0.5, "lastmod": NOW - 10 * DAY, "etag": "e0", "hist": 0.2},
                {"url": "u2", "imp": 0.8, "lastmod": NOW - 2 * DAY, "etag": "a", "hist": 0.5},
                {"url": "u3", "imp": 0.1, "lastmod": NOW - 20 * DAY, "etag": "c", "hist": 0.0},
            ],
            "etag_now": {"u1": "e9", "u2": "a", "u3": "c"},
        }

    def test_dedup_merge(self):
        out = prio.run(self._base())
        urls = [d["url"] for d in out["decisions"]]
        self.assertEqual(sorted(urls), ["u1", "u2", "u3"])
        d1 = next(d for d in out["decisions"] if d["url"] == "u1")
        self.assertEqual(d1["merged_from"], 2)          # 同 URL 两条已合并
        self.assertTrue(d1["signals"]["etag_changed"])  # 取最新 lastmod 携带的 etag=e1 -> e9

    def test_shuffle_invariant(self):
        rnd = random.Random(7)
        base = self._base()
        ref = prio.run(copy.deepcopy(base))
        for _ in range(10):
            f = copy.deepcopy(base)
            rnd.shuffle(f["pages"])
            items = list(f["etag_now"].items())
            rnd.shuffle(items)
            f["etag_now"] = dict(items)
            got = prio.run(f)
            for k in ("queue", "skipped", "fast_lane", "coverage", "digest",
                      "distribution", "decisions", "coverage_stats"):
                self.assertEqual(got[k], ref[k], "重排后字段变化: " + k)


class FastLaneAndBudgetTests(unittest.TestCase):
    def test_fast_lane_free_of_budget(self):
        sig = {
            "now": NOW, "budget": 2, "horizon": 7,
            "pages": [
                {"url": "a", "imp": 0.9, "lastmod": NOW - DAY, "etag": "x", "hist": 1},
                {"url": "b", "imp": 0.8, "lastmod": NOW - DAY, "etag": "y", "hist": 1},
                {"url": "c", "imp": 0.65, "lastmod": NOW - DAY, "etag": "z", "hist": 1},
                {"url": "new1", "imp": 0.95},
                {"url": "new2", "imp": 0.30},
            ],
            "etag_now": {"a": "x", "b": "y", "c": "z"},
        }
        out = prio.run(sig)
        self.assertEqual(len(out["queue"]), 2)          # budget 不被快速通道占用
        self.assertEqual(out["fast_lane"], ["new1", "new2"])
        self.assertEqual(out["skipped"], ["c"])
        cs = out["coverage_stats"]
        self.assertEqual(cs["scheduled"], 4)
        self.assertEqual(cs["fast_lane_count"], 2)
        # 加权覆盖按实际入选集计算: 预算2 + 快速通道2
        self.assertAlmostEqual(
            out["coverage"],
            round((0.9 + 0.8 + 0.95 + 0.30) / (0.9 + 0.8 + 0.65 + 0.95 + 0.30), 6))
        # 高重要度页面 (a/b/new1) 全部被抓, 高档重要度覆盖率 100%
        self.assertEqual(cs["importance_coverage"]["high"]["scheduled"], 3)
        self.assertEqual(cs["importance_coverage"]["high"]["coverage"], 1.0)
        # 新页无 lastmod/etag 但 etag_now 有记录时不算新 URL, 走普通队列
        sig["etag_now"]["new1"] = "probe"
        out2 = prio.run(sig)
        self.assertEqual(out2["fast_lane"], ["new2"])


class TwoCycleAcceptanceTests(unittest.TestCase):
    def test_simulator_acceptance(self):
        cwd = os.getcwd()
        try:
            simulator.main()
            with open("sim_report.json") as f:
                rep = json.load(f)
        finally:
            pass
        acc = rep["acceptance"]
        self.assertTrue(acc["high_changed_all_crawled"])
        self.assertGreaterEqual(acc["min_unchanged_reduction"], 0.60)
        self.assertTrue(acc["shuffle_invariant"])
        self.assertEqual(len(rep["cycles"]), 2)
        for c in rep["cycles"]:
            self.assertFalse(c["high_changed_missed"])
            self.assertLessEqual(len(c["queue"]), c["budget"])
        # 两周期 digest 不同, 证明调度随信号变化而更新
        self.assertNotEqual(rep["cycles"][0]["digest"], rep["cycles"][1]["digest"])


if __name__ == "__main__":
    unittest.main(verbosity=2)