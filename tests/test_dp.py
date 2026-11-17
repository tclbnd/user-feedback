"""Acceptance tests for cost-based join ordering (DP over subsets)."""
import json
import os
import sys
import unittest
from functools import lru_cache
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cost import Query, table_of  # noqa: E402
from joinplan import execute  # noqa: E402
from planner import choose, strip, written  # noqa: E402

EXAMPLES = ("star.json", "chain.json", "outer.json", "transitive.json")


def load(name):
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as fh:
        return json.load(fh)


def walk(node):
    yield node
    if node["op"] == "join":
        yield from walk(node["left"])
        yield from walk(node["right"])


def tables_under(node):
    return {n["table"] for n in walk(node) if n["op"] == "scan"}


def canon(rows):
    return sorted(json.dumps(r, sort_keys=True) for r in rows)


def reference(q):
    """Cheapest plan cost by exhaustive enumeration of every valid join tree,
    computed straight from the cost formulas (independent of planner.choose)."""
    nullable = q.nullable
    adj = {t: set() for t in q.order}
    for cls in q.classes:
        ts = [table_of(c) for c in cls]
        for t in ts:
            adj[t].update(ts)
    for t, on in nullable.items():
        for pair in on:
            for col in pair:
                adj[t].add(table_of(col))
                adj[table_of(col)].add(t)
    seen, stack = set(), [q.order[0]]
    while stack:
        t = stack.pop()
        if t not in seen:
            seen.add(t)
            stack.extend(adj[t])
    connected = seen == set(q.order)

    @lru_cache(maxsize=None)
    def plans(subset):
        """All (cost, rows) pairs achievable by valid plans over the subset."""
        if len(subset) == 1:
            t = next(iter(subset))
            fs = [] if t in nullable else [f for f in q.filters if table_of(f["col"]) == t]
            return [(q.stats[t]["rows"], q.scan_rows(t, fs))]
        out = []
        for t in subset:  # a left-joined table enters alone, on the right
            if t in nullable:
                deps = {table_of(c) for p in nullable[t] for c in p} - {t}
                if deps <= subset - {t}:
                    rrows = q.stats[t]["rows"]
                    fs = [f for f in q.filters if table_of(f["col"]) == t]
                    for lc, lr in plans(subset - {t}):
                        rows = q.left_rows(lr, rrows, nullable[t], fs)
                        out.append((lc + rrows + rows, rows))
        for r in range(1, len(subset)):  # inner-join splits
            for ls in combinations(sorted(subset), r):
                lset, rset = frozenset(ls), subset - frozenset(ls)
                if q.shape == "left_deep" and len(rset) != 1:
                    continue
                if len(lset) == 1 and lset <= set(nullable):
                    continue
                if len(rset) == 1 and rset <= set(nullable):
                    continue
                if connected and not q.spanning(lset, rset):
                    continue
                for lc, lr in plans(lset):
                    for rc, rr in plans(rset):
                        rows = q.inner_rows(lr, rr, lset, rset)
                        out.append((lc + rc + rows, rows))
        return out

    return min(c for c, _ in plans(frozenset(q.order)))


class CostTests(unittest.TestCase):
    def check_optimal(self, raw):
        q = Query(raw)
        plan = choose(q)
        self.assertAlmostEqual(plan["cost"], reference(q))
        return q, plan

    def test_star_optimal(self):
        q, plan = self.check_optimal(load("star.json"))
        self.assertLess(plan["cost"], written(q)["cost"])

    def test_chain_optimal(self):
        q, plan = self.check_optimal(load("chain.json"))
        self.assertLess(plan["cost"], written(q)["cost"])

    def test_outer_optimal(self):
        self.check_optimal(load("outer.json"))

    def test_transitive_optimal(self):
        self.check_optimal(load("transitive.json"))

    def test_left_deep_optimal(self):
        for name in EXAMPLES:
            raw = load(name)
            raw["shape"] = "left_deep"
            q, plan = self.check_optimal(raw)
            for node in walk(strip(plan)):
                if node["op"] == "join":
                    self.assertEqual(node["right"]["op"], "scan", name)
            bushy = choose(Query(load(name)))
            self.assertLessEqual(bushy["cost"], plan["cost"], name)


class StructureTests(unittest.TestCase):
    def test_no_cross_when_connected(self):
        for name in EXAMPLES:
            plan = strip(choose(Query(load(name))))
            self.assertNotIn("cross", {n.get("type") for n in walk(plan)}, name)

    def test_cross_when_disconnected(self):
        raw = {"tables": {"a": {"rows": 100, "ndv": {"id": 100}},
                          "b": {"rows": 50, "ndv": {"aid": 100}},
                          "c": {"rows": 40, "ndv": {"id": 40}},
                          "d": {"rows": 30, "ndv": {"cid": 40}}},
               "from": [{"table": "a"}, {"table": "b", "on": [["a.id", "b.aid"]]},
                        {"table": "c"}, {"table": "d", "on": [["c.id", "d.cid"]]}]}
        q = Query(raw)
        plan = strip(choose(q))
        crosses = [n for n in walk(plan) if n.get("type") == "cross"]
        self.assertEqual(len(crosses), 1)
        self.assertAlmostEqual(choose(q)["cost"], reference(q))

    def test_transitive_equality_merges_classes(self):
        q = Query(load("transitive.json"))
        self.assertIn({"a.x", "b.x", "c.x"}, q.classes)
        # c has no written `on`; it joins through the derived equality
        plan = strip(choose(q))
        self.assertNotIn("cross", {n.get("type") for n in walk(plan)})

    def test_outer_join_placement(self):
        q = Query(load("outer.json"))
        plan = strip(choose(q))
        for node in walk(plan):
            if node.get("type") == "left":
                right = node["right"]
                self.assertEqual(right["op"], "scan")
                self.assertIn(right["table"], q.nullable)
                deps = {table_of(c) for p in q.nullable[right["table"]] for c in p} - {right["table"]}
                self.assertLessEqual(deps, tables_under(node["left"]))
            if node.get("type") == "inner":
                for side in (node["left"], node["right"]):
                    self.assertFalse(side["op"] == "scan" and side["table"] in q.nullable)

    def test_filter_pushdown(self):
        q = Query(load("outer.json"))
        plan = strip(choose(q))
        scans = {n["table"]: n for n in walk(plan) if n["op"] == "scan"}
        self.assertEqual([f["col"] for f in scans["o"]["filters"]], ["o.st"])
        self.assertEqual(scans["r"]["filters"], [])  # left-joined table: scan stays unfiltered
        lefts = {n["right"]["table"]: n for n in walk(plan) if n.get("type") == "left"}
        self.assertEqual([f["col"] for f in lefts["r"]["filters"]], ["r.score"])


class ExecutionTests(unittest.TestCase):
    def test_same_result_as_written_order(self):
        for name in EXAMPLES:
            raw = load(name)
            q = Query(raw)
            got = execute(strip(choose(q)), q, raw["data"])
            want = execute(strip(written(q)), q, raw["data"])
            self.assertEqual(canon(got), canon(want), name)

    def test_left_deep_same_result(self):
        for name in EXAMPLES:
            raw = load(name)
            raw["shape"] = "left_deep"
            q = Query(raw)
            got = execute(strip(choose(q)), q, raw["data"])
            want = execute(strip(written(q)), q, raw["data"])
            self.assertEqual(canon(got), canon(want), name)


if __name__ == "__main__":
    unittest.main()

