#!/usr/bin/env python3
"""topo.py 的验收测试：稳定性、成环拒绝、局部更新规模、下游完整计划。"""

import heapq
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

import topo


def reference_order(edges, n):
    """独立参考实现：全量 Kahn（小根堆），用来验证计划是合法拓扑序。"""
    succ = [[] for _ in range(n)]
    indeg = [0] * n
    for a, b in edges:
        succ[a].append(b)
        indeg[b] += 1
    heap = [v for v in range(n) if indeg[v] == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        u = heapq.heappop(heap)
        out.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                heapq.heappush(heap, v)
    return out


def is_topo(order, edges, n):
    if sorted(order) != list(range(n)):
        return False
    pos = {v: i for i, v in enumerate(order)}
    return all(pos[a] < pos[b] for a, b in edges)


def final_edges(ops):
    live = set()
    for op in ops:
        key = (op["a"], op["b"])
        if op["op"] == "add":
            live.add(key)
        else:
            live.discard(key)
    return live


def final_live(ops, rejected):
    """回放事件求最终存活边集；被拒的成环加边不生效。"""
    bad = {(x["op"]["a"], x["op"]["b"]) for x in rejected}
    live = set()
    for op in ops:
        key = (op["a"], op["b"])
        if op["op"] == "add":
            if key not in bad:
                live.add(key)
        else:
            live.discard(key)
    return live


PARSE = "ops.json"
OUT = "output.json"


def run_cli(sc, tmpdir):
    src = os.path.join(tmpdir, PARSE)
    dst = os.path.join(tmpdir, OUT)
    with open(src, "w", encoding="utf-8") as f:
        json.dump(sc, f)
    subprocess.run([sys.executable, "topo.py", src, dst], check=True)
    with open(dst, "r", encoding="utf-8") as f:
        return json.load(f)


def reference_levels(edges, n):
    """独立参考实现：全量重算 level（最长依赖链）与邻接表。"""
    succ = [[] for _ in range(n)]
    pred = [[] for _ in range(n)]
    indeg = [0] * n
    for a, b in edges:
        succ[a].append(b)
        pred[b].append(a)
        indeg[b] += 1
    lev = [0] * n
    stack = [v for v in range(n) if indeg[v] == 0]
    while stack:
        u = stack.pop()
        for w in succ[u]:
            if lev[w] < lev[u] + 1:
                lev[w] = lev[u] + 1
            indeg[w] -= 1
            if indeg[w] == 0:
                stack.append(w)
    return lev, succ, pred


def reaches(edges, s, t):
    """独立参考实现：边集里 s 是否可达 t。"""
    if s == t:
        return True
    succ = {}
    for a, b in edges:
        succ.setdefault(a, []).append(b)
    seen = {s}
    stack = [s]
    while stack:
        u = stack.pop()
        for w in succ.get(u, ()):
            if w == t:
                return True
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return False


class TestAgainstFullRecompute(unittest.TestCase):
    """逐步把增量状态与「每步全量重算」对拍。"""

    def test_random_op_streams(self):
        rng = random.Random(3)
        for trial in range(120):
            n = rng.randint(2, 22)
            ops = []
            for _ in range(rng.randint(1, 90)):
                a, b = rng.randrange(n), rng.randrange(n)
                ops.append((rng.choice(["add", "del"]), a, b))

            planner = topo.IncrementalPlanner(n)
            live = set()
            for step, (kind, a, b) in enumerate(ops):
                ctx = "trial %d step %d op %s" % (trial, step, (kind, a, b))
                if kind == "add":
                    expect_cycle = (a == b) or reaches(live, b, a)
                    cyc = planner.add_edge(a, b)
                    if expect_cycle:
                        self.assertIsNotNone(cyc, ctx)
                        # 环路径可复核：[a, b, ..., a]，首段是新边，其余是现存边
                        self.assertEqual(cyc[0], a, ctx)
                        self.assertEqual(cyc[-1], a, ctx)
                        self.assertEqual(cyc[1], b, ctx)
                        for u, v in zip(cyc[1:], cyc[2:]):
                            self.assertIn((u, v), live, ctx)
                        self.assertEqual(len(set(cyc[:-1])), len(cyc) - 1, ctx)
                    else:
                        self.assertIsNone(cyc, ctx)
                        live.add((a, b))
                else:
                    planner.del_edge(a, b)
                    live.discard((a, b))

                lev, succ, pred = reference_levels(live, n)
                self.assertEqual(planner.level, lev, ctx)
                self.assertEqual(planner.succ, [set(s) for s in succ], ctx)
                self.assertEqual(planner.pred, [set(p) for p in pred], ctx)
                order = planner.order()
                self.assertTrue(is_topo(order, live, n), ctx)
                self.assertEqual(order, sorted(range(n), key=lambda v: (lev[v], v)),
                                 ctx)
                self.assertTrue(planner.validate(order), ctx)


class TestCycles(unittest.TestCase):
    def test_self_loop_rejected(self):
        r = topo.run({"n": 2, "ops": [{"op": "add", "a": 1, "b": 1}]})
        self.assertEqual(len(r["rejected"]), 1)
        self.assertEqual(r["rejected"][0]["cycle"], [1, 1])
        self.assertTrue(r["valid"])
        self.assertEqual(len(r["order"]), 2)

    def test_cycle_keeps_old_graph_and_reports_path(self):
        ops = [
            {"op": "add", "a": 0, "b": 1},
            {"op": "add", "a": 1, "b": 2},
            {"op": "add", "a": 2, "b": 3},
            {"op": "add", "a": 3, "b": 0},
        ]
        r = topo.run({"n": 5, "ops": ops})
        self.assertEqual(len(r["rejected"]), 1)
        cyc = r["rejected"][0]["cycle"]
        self.assertEqual(cyc[0], 3)          # 以新边的起点开头
        self.assertEqual(cyc[-1], 3)         # 首尾相同
        self.assertEqual(cyc[0:3], [3, 0, 1])  # 按现存依赖 3->0->1...
        # 被拒后旧图仍在：3->0 不存在，计划仍是合法拓扑序
        self.assertTrue(is_topo(r["order"], final_edges(ops[:3]), 5))
        self.assertTrue(r["valid"])
        self.assertEqual(r["rejected"][0]["op"], ops[3])

    def test_cycle_path_is_reviewable(self):
        ops = [{"op": "add", "a": 0, "b": 1}, {"op": "add", "a": 1, "b": 2},
               {"op": "add", "a": 2, "b": 3}, {"op": "add", "a": 1, "b": 3},
               {"op": "add", "a": 3, "b": 0}]
        r = topo.run({"n": 4, "ops": ops})
        self.assertEqual(len(r["rejected"]), 1)
        cyc = r["rejected"][0]["cycle"]
        self.assertEqual((cyc[0], cyc[-1]), (3, 3))
        self.assertEqual((cyc[0], cyc[1]), (3, 0))   # 首段是新边
        # 其余各段都是现存依赖边，沿路径确实回到起点
        live = final_edges(ops[:-1])
        for u, v in zip(cyc[1:], cyc[2:]):
            self.assertIn((u, v), live)
        self.assertEqual(cyc, [3, 0, 1, 3])


class TestStabilityAndValidity(unittest.TestCase):
    def test_order_is_independent_of_insertion_order(self):
        rng = random.Random(2024)
        n = 60
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        rng.shuffle(pairs)
        edges = [e for e in pairs if rng.random() < 0.06][:120]
        plans = []
        for _ in range(5):
            shuffled = edges[:]
            rng.shuffle(shuffled)
            r = topo.run({"n": n, "ops": [{"op": "add", "a": a, "b": b}
                                          for a, b in shuffled]})
            self.assertTrue(r["valid"])
            self.assertTrue(is_topo(r["order"], set(edges), n))
            plans.append(r["order"])
        for p in plans[1:]:
            self.assertEqual(p, plans[0])

    def test_order_is_independent_of_interleaved_del(self):
        rng = random.Random(7)
        n = 40
        base = [(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < 0.05]
        extra = [(i, j) for i in range(n) for j in range(i + 1, n)
                 if rng.random() < 0.05 and (i, j) not in base][:30]

        def build(with_noise, seed):
            r2 = random.Random(seed)
            ops = []
            noise = extra[:]
            r2.shuffle(noise)
            for e in base:
                ops.append({"op": "add", "a": e[0], "b": e[1]})
                if with_noise and noise:
                    x = noise.pop()
                    ops.append({"op": "add", "a": x[0], "b": x[1]})
                    ops.append({"op": "del", "a": x[0], "b": x[1]})
            ops = [o for o in ops]
            return topo.run({"n": n, "ops": ops})

        a = build(False, 1)
        b = build(True, 2)
        self.assertEqual(a["order"], b["order"])

    def test_each_work_item_exactly_once_with_real_deletes(self):
        rng = random.Random(11)
        n = 50
        ops = []
        live = set()
        for _ in range(400):
            a = rng.randrange(n)
            b = rng.randrange(n)
            if a == b:
                continue
            if rng.random() < 0.6:
                ops.append({"op": "add", "a": a, "b": b})
                live.add((a, b))
            else:
                ops.append({"op": "del", "a": a, "b": b})
                live.discard((a, b))
        r = topo.run({"n": n, "ops": ops})
        self.assertTrue(r["valid"])
        self.assertEqual(sorted(r["order"]), list(range(n)))
        # 最终图 = 现存边；被拒的加边不在其中
        accepted = final_edges(ops)
        for rej in r["rejected"]:
            accepted.discard((rej["op"]["a"], rej["op"]["b"]))
        self.assertTrue(is_topo(r["order"], accepted, n))


class TestIncrementalLocality(unittest.TestCase):
    def _chain(self, n=2000):
        return {"n": n, "ops": [{"op": "add", "a": i, "b": i + 1} for i in range(n - 1)]}

    def test_full_recompute_count_is_zero(self):
        r = topo.run(self._chain(200))
        self.assertEqual(r["stats"]["recomputes"], 0)

    def test_visit_scope_far_below_per_op_full_rebuild(self):
        r = topo.run(self._chain(2000))
        per_op_full = 2000 * 1999
        self.assertLess(r["stats"]["affected_sum"], per_op_full // 20)

    def test_deleting_edge_still_yields_complete_plan(self):
        n = 30
        ops = [{"op": "add", "a": i, "b": i + 1} for i in range(n - 1)]
        ops.append({"op": "del", "a": 10, "b": 11})
        r = topo.run({"n": n, "ops": ops})
        self.assertTrue(r["valid"])
        self.assertEqual(sorted(r["order"]), list(range(n)))
        self.assertTrue(is_topo(r["order"], final_edges(ops), n))

    def test_remove_stale_dependency_of_downstream(self):
        n = 10
        ops = [
            {"op": "add", "a": 0, "b": 5},
            {"op": "add", "a": 5, "b": 6},
            {"op": "add", "a": 0, "b": 6},   # 0 -> 6 直连（冗余/过时依赖）
            {"op": "add", "a": 6, "b": 7},
            {"op": "add", "a": 7, "b": 8},
            {"op": "del", "a": 0, "b": 6},   # 删掉过时依赖
        ]
        r = topo.run({"n": n, "ops": ops})
        self.assertTrue(r["valid"])
        self.assertEqual(sorted(r["order"]), list(range(n)))
        self.assertTrue(is_topo(r["order"], final_edges(ops), n))
        pos = {v: i for i, v in enumerate(r["order"])}
        self.assertLess(pos[0], pos[5])
        self.assertLess(pos[5], pos[6])
        self.assertLess(pos[6], pos[7])
        self.assertLess(pos[7], pos[8])


class TestAcceptance(unittest.TestCase):
    def _deep_ops(self, n=10000, total=1000, seed=1):
        """恰好 total 个事件：保活长链 + 随机插删 + 末尾必然成环的加边。"""
        rng = random.Random(seed)
        deep = list(range(0, n, n // 10))
        reserved = set(zip(deep, deep[1:]))
        ops = [{"op": "add", "a": a, "b": b} for a, b in reserved]
        live = set(reserved)
        while len(ops) < total - 1:
            pool = live - reserved
            r = rng.random()
            if r < 0.6 or not pool:
                ops.append({"op": "add", "a": rng.randrange(n), "b": rng.randrange(n)})
            elif r < 0.88:
                a, b = rng.choice(sorted(pool))
                ops.append({"op": "del", "a": a, "b": b})
                live.discard((a, b))
            else:
                a, b = rng.choice(sorted(pool))
                ops.append({"op": "add", "a": a, "b": b})
        ops.append({"op": "add", "a": deep[-1], "b": deep[0]})
        return ops, reserved

    def test_10k_tasks_1k_ops(self):
        n = 10000
        ops, reserved = self._deep_ops(n)
        self.assertEqual(len(ops), 1000)
        r = topo.run({"n": n, "ops": ops})
        self.assertTrue(r["valid"])
        self.assertEqual(len(r["order"]), n)
        self.assertEqual(len(set(r["order"])), n)

        live = final_live(ops, r["rejected"])
        self.assertTrue(is_topo(r["order"], live, n))

        # 成环输入给出可复核路径：首尾相同，覆盖整条保留长链
        self.assertEqual(len(r["rejected"]), 1)
        cyc = r["rejected"][0]["cycle"]
        self.assertEqual((cyc[0], cyc[-1]), (9000, 9000))
        self.assertEqual(len(cyc), len(reserved) + 2)   # 长链 10 节点 + 回边
        self.assertEqual(set(cyc[:-1]), {a for a, _ in reserved} | {9000})
        for u, v in zip(cyc[1:], cyc[2:]):
            self.assertIn((u, v), reserved)

        # 同一最终图、另一接入顺序 -> 逐元素相同的计划
        rng = random.Random(2)
        adds = [{"op": "add", "a": a, "b": b} for a, b in live]
        rng.shuffle(adds)
        r2 = topo.run({"n": n, "ops": adds})
        self.assertEqual(r2["order"], r["order"])
        self.assertEqual(r2["rejected"], [])
        self.assertTrue(r2["valid"])

    def test_local_updates_beat_per_op_full_rebuild(self):
        n = 10000
        ops, _ = self._deep_ops(n)
        r = topo.run({"n": n, "ops": ops})
        self.assertEqual(r["stats"]["recomputes"], 0)
        per_op_full = n * len(ops)          # 逐次全量重建的访问量下界
        self.assertLess(r["stats"]["affected_sum"], per_op_full // 100)

    def test_10k_ops_visit_scope(self):
        rng = random.Random(5)
        n = 10000
        ops = [{"op": "add", "a": i, "b": i + 1} for i in range(n - 1)]
        for _ in range(1000):
            a, b = rng.randrange(n), rng.randrange(n)
            ops.append({"op": "add" if rng.random() < 0.5 else "del", "a": a, "b": b})
        r = topo.run({"n": n, "ops": ops})
        self.assertTrue(r["valid"])
        self.assertEqual(r["stats"]["recomputes"], 0)
        # 逐次全量重建至少要 n * (n + ops) 次处理
        self.assertLess(r["stats"]["affected_sum"], n * (n + len(ops)) // 100)


class TestCli(unittest.TestCase):
    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            sc = {"n": 6, "ops": [
                {"op": "add", "a": 0, "b": 1},
                {"op": "add", "a": 1, "b": 2},
                {"op": "add", "a": 3, "b": 4},
                {"op": "del", "a": 3, "b": 4},
                {"op": "add", "a": 2, "b": 0},
            ]}
            r = run_cli(sc, d)
            self.assertTrue(r["valid"])
            self.assertEqual(sorted(r["order"]), list(range(6)))
            self.assertEqual(len(r["rejected"]), 1)
            self.assertEqual(r["rejected"][0]["cycle"][0], 2)
            self.assertEqual(r["rejected"][0]["cycle"][-1], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
