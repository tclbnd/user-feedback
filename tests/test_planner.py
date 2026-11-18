import json
import math
import os
import sys
import unittest
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cost import Query, table_of  # noqa: E402
from joinplan import execute  # noqa: E402
from planner import choose, connected, strip, written  # noqa: E402

Q = {"tables": {"a": {"rows": 100, "ndv": {"id": 100}}, "b": {"rows": 400, "ndv": {"aid": 100, "v": 4}}},
     "from": [{"table": "a"}, {"table": "b", "on": [["a.id", "b.aid"]]}],
     "where": [{"col": "b.v", "op": "=", "value": 1}],
     "data": {"a": [{"id": 1}, {"id": 2}], "b": [{"aid": 1, "v": 1}, {"aid": 2, "v": 3}, {"aid": 1, "v": 1}]}}

DISCONNECTED = {"tables": {"p": {"rows": 100, "ndv": {"id": 100}},
                           "q": {"rows": 500, "ndv": {"pid": 100, "id": 500}},
                           "r": {"rows": 200, "ndv": {"id": 200}},
                           "s": {"rows": 50, "ndv": {"rid": 200, "id": 50}}},
                "from": [{"table": "p"}, {"table": "q", "on": [["p.id", "q.pid"]]},
                         {"table": "r"}, {"table": "s", "on": [["r.id", "s.rid"]]}],
                "data": {"p": [{"id": 1}, {"id": 2}],
                         "q": [{"pid": 1, "id": 10}, {"pid": 2, "id": 11}],
                         "r": [{"id": 5}, {"id": 6}],
                         "s": [{"rid": 5, "id": 50}, {"rid": 7, "id": 51}]}}

EXAMPLES = ["star.json", "chain.json", "outer.json", "transitive.json"]
SHAPES = ["bushy", "left_deep"]


def load(name, shape=None):
    with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as fh:
        raw = json.load(fh)
    if shape:
        raw["shape"] = shape
    return raw


def run(plan, q, data):
    rows = execute(strip(plan), q, data)
    cols = sorted({c for r in rows for c in r})
    return sorted(([r.get(c) for c in cols] for r in rows), key=json.dumps)


def nodes(plan):
    yield plan
    if plan["op"] == "join":
        yield from nodes(plan["left"])
        yield from nodes(plan["right"])


def brute_best(q):
    """Reference: exhaustively enumerate every valid plan, return the cheapest cost."""
    nullable = set(q.nullable)
    allow_cross = not connected(q)
    memo = {}

    def plans(subset):
        if subset in memo:
            return memo[subset]
        out = []
        if len(subset) == 1:
            (table,) = tuple(subset)
            if table not in nullable:
                filters = [f for f in q.filters if table_of(f["col"]) == table]
                out.append((q.scan_rows(table, filters), q.stats[table]["rows"]))
        else:
            for table in subset & nullable:
                on = q.nullable[table]
                if {table_of(c) for p in on for c in p} - {table} <= subset - {table}:
                    filters = [f for f in q.filters if table_of(f["col"]) == table]
                    for lrows, lcost in plans(subset - {table}):
                        rows = q.left_rows(lrows, q.stats[table]["rows"], on, filters)
                        out.append((rows, lcost + q.stats[table]["rows"] + rows))
            if q.shape == "left_deep":
                splits = [(subset - {t}, frozenset([t])) for t in sorted(subset)]
            else:
                splits = []
                tabs = sorted(subset)
                for size in range(1, len(tabs)):
                    for combo in combinations(tabs, size):
                        left = frozenset(combo)
                        splits.append((left, subset - left))
            for left_set, right_set in splits:
                if not allow_cross and not q.spanning(left_set, right_set):
                    continue
                for lrows, lcost in plans(left_set):
                    for rrows, rcost in plans(right_set):
                        rows = q.inner_rows(lrows, rrows, left_set, right_set)
                        out.append((rows, lcost + rcost + rows))
        memo[subset] = out
        return out

    return min(cost for _, cost in plans(frozenset(q.order)))


class PlannerTests(unittest.TestCase):
    def test_scan_estimate(self):
        plan = choose(Query(Q))
        scans = {n["table"]: n for n in (plan["left"], plan["right"])}
        self.assertAlmostEqual(scans["b"]["rows"], 100.0)
        self.assertEqual(scans["a"]["cost"], 100)

    def test_execute_two_tables(self):
        q = Query(Q)
        rows = execute(strip(choose(q)), q, Q["data"])
        self.assertEqual(sorted(r["a.id"] for r in rows), [1, 1])


class CostModelTests(unittest.TestCase):
    def test_classes_merge_transitively(self):
        q = Query(load("transitive.json"))
        self.assertIn({"x.k", "y.k", "z.k", "w.k"}, q.classes)

    def test_inner_join_uses_max_ndv(self):
        q = Query(Q)
        # ndv(a.id) = 100, ndv(b.aid) = 100 -> 100 * 400 / 100
        self.assertAlmostEqual(q.inner_rows(100, 400, {"a"}, {"b"}), 400.0)
        q2 = Query({"tables": {"u": {"rows": 10, "ndv": {"k": 50}},
                               "v": {"rows": 10, "ndv": {"k": 200}}},
                    "from": [{"table": "u"}, {"table": "v", "on": [["u.k", "v.k"]]}]})
        # selectivity is 1 / max(50, 200)
        self.assertAlmostEqual(q2.inner_rows(10, 10, {"u"}, {"v"}), 0.5)

    def test_left_join_rows_never_below_left(self):
        q = Query(load("outer.json"))
        on = [("o.id", "r.oid")]
        rows = q.left_rows(20000, 15000, on, [])
        self.assertAlmostEqual(rows, 20000.0)  # max(rows(L), inner = 15000)

    def test_filter_selectivities(self):
        q = Query(Q)
        self.assertAlmostEqual(q.filter_sel({"col": "b.v", "op": "=", "value": 1}), 1 / 4)
        self.assertAlmostEqual(q.filter_sel({"col": "b.v", "op": "!=", "value": 1}), 3 / 4)
        self.assertAlmostEqual(q.filter_sel({"col": "b.v", "op": "<", "value": 1}), 1 / 3)


class OptimizerTests(unittest.TestCase):
    def test_cost_matches_brute_force(self):
        for name in EXAMPLES:
            for shape in SHAPES:
                with self.subTest(example=name, shape=shape):
                    q = Query(load(name, shape))
                    plan = choose(q)
                    self.assertTrue(math.isclose(plan["cost"], brute_best(q), rel_tol=1e-9))

    def test_not_worse_than_written_order(self):
        for name in EXAMPLES:
            for shape in SHAPES:
                with self.subTest(example=name, shape=shape):
                    q = Query(load(name, shape))
                    self.assertLessEqual(choose(q)["cost"], written(q)["cost"] + 1e-6)

    def test_execution_matches_written_order(self):
        for name in EXAMPLES:
            for shape in SHAPES:
                with self.subTest(example=name, shape=shape):
                    raw = load(name, shape)
                    q = Query(raw)
                    self.assertEqual(run(choose(q), q, raw["data"]),
                                     run(written(q), q, raw["data"]))

    def test_no_cross_product_when_connected(self):
        for name in EXAMPLES:
            for shape in SHAPES:
                with self.subTest(example=name, shape=shape):
                    q = Query(load(name, shape))
                    self.assertTrue(connected(q))
                    for node in nodes(choose(q)):
                        if node["op"] == "join":
                            self.assertNotEqual(node["type"], "cross")
                            self.assertTrue(node["on"])


class OuterJoinTests(unittest.TestCase):
    def setUp(self):
        self.q = Query(load("outer.json"))
        self.plan = choose(self.q)

    def test_left_join_placement(self):
        for node in nodes(self.plan):
            if node["op"] != "join":
                continue
            if node["type"] == "left":
                right = node["right"]
                self.assertEqual(right["op"], "scan")
                self.assertIn(right["table"], self.q.nullable)
                on_tables = {table_of(c) for p in self.q.nullable[right["table"]] for c in p}
                self.assertLessEqual(on_tables - {right["table"]}, node["left"]["tables"])
            else:
                for side in (node["left"], node["right"]):
                    self.assertFalse(side["op"] == "scan" and side["table"] in self.q.nullable)

    def test_filter_on_nullable_table_at_join_node(self):
        scans = {n["table"]: n for n in nodes(self.plan) if n["op"] == "scan"}
        self.assertEqual(scans["r"]["filters"], [])  # nullable scan carries no filters
        self.assertEqual([f["col"] for n in nodes(self.plan) if n["op"] == "scan"
                          for f in n["filters"]], ["o.st"])
        lefts = [n for n in nodes(self.plan) if n["op"] == "join" and n["type"] == "left"]
        r_join = next(n for n in lefts if n["right"]["table"] == "r")
        self.assertEqual([f["col"] for f in r_join["filters"]], ["r.score"])

    def test_left_join_estimate(self):
        r_join = next(n for n in nodes(self.plan)
                      if n["op"] == "join" and n["type"] == "left" and n["right"]["table"] == "r")
        left = r_join["left"]
        inner = left["rows"] * 15000 / 20000
        expected = max(left["rows"], inner) / 3  # r.score > 3 selectivity
        self.assertAlmostEqual(r_join["rows"], expected)


class LeftDeepTests(unittest.TestCase):
    def test_right_input_is_single_table(self):
        for name in EXAMPLES:
            with self.subTest(example=name):
                q = Query(load(name, "left_deep"))
                for node in nodes(choose(q)):
                    if node["op"] == "join":
                        self.assertEqual(node["right"]["op"], "scan")


class CrossProductTests(unittest.TestCase):
    def test_cross_only_when_disconnected(self):
        q = Query(DISCONNECTED)
        self.assertFalse(connected(q))
        plan = choose(q)
        crosses = [n for n in nodes(plan) if n["op"] == "join" and n["type"] == "cross"]
        self.assertEqual(len(crosses), 1)

    def test_disconnected_cost_matches_brute_force(self):
        for shape in SHAPES:
            with self.subTest(shape=shape):
                raw = dict(DISCONNECTED, shape=shape)
                q = Query(raw)
                self.assertTrue(math.isclose(choose(q)["cost"], brute_best(q), rel_tol=1e-9))

    def test_disconnected_execution_matches_written_order(self):
        q = Query(DISCONNECTED)
        self.assertEqual(run(choose(q), q, DISCONNECTED["data"]),
                         run(written(q), q, DISCONNECTED["data"]))


if __name__ == "__main__":
    unittest.main()
