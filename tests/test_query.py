import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query import run  # noqa: E402

DB = {
    "orders": {"not_null": ["id", "cust"], "rows": [{"id": 1, "cust": 7}, {"id": 2, "cust": 8}, {"id": 3, "cust": 9}]},
    "vip": {"not_null": ["cust"], "rows": [{"cust": 7}, {"cust": 9}]},
}

# Outer rows hold NULL keys (id 2) and a NULL correlation column (id 4);
# the inner table holds a NULL value row (b2) and a NULL region row (b4).
DB1 = {
    "o": {"not_null": ["id"], "rows": [
        {"id": 1, "k": 10, "region": "n"},
        {"id": 2, "k": None, "region": "n"},
        {"id": 3, "k": 20, "region": "s"},
        {"id": 4, "k": 30, "region": None},
        {"id": 5, "k": 9, "region": "s"},
    ]},
    "b": {"not_null": [], "rows": [
        {"v": 10, "region": "n"},
        {"v": None, "region": "n"},
        {"v": 20, "region": "s"},
        {"v": 40, "region": None},
    ]},
}

# Two-column, fully nullable tables, including the (1, NULL) pair on both
# sides, which Python tuple equality would wrongly treat as a matching row.
DB2 = {
    "t": {"not_null": [], "rows": [
        {"a": 1, "b": 10},
        {"a": 2, "b": None},
        {"a": 3, "b": 30},
        {"a": None, "b": 40},
        {"a": 1, "b": None},
    ]},
    "s": {"not_null": [], "rows": [
        {"x": 1, "y": 10},
        {"x": 1, "y": None},
        {"x": 2, "y": 99},
        {"x": None, "y": 10},
    ]},
}

# Same shape as DB2 but every compared column is declared NOT NULL.
DB3 = {
    "p": {"not_null": ["a", "b"], "rows": [
        {"a": 1, "b": 10}, {"a": 2, "b": 20}, {"a": 3, "b": 30},
    ]},
    "q": {"not_null": ["x", "y"], "rows": [
        {"x": 1, "y": 10}, {"x": 2, "y": 20},
    ]},
}


def col(name):
    return {"col": name}


def lit(value):
    return {"value": value}


def eq(left, right):
    return {"op": "=", "left": left, "right": right}


def is_not_null(arg):
    return {"op": "is_not_null", "arg": arg}


def subquery_pred(op, args, select, inner_where=None):
    pred = {"op": op, "query": {"from": "b", "as": "b", "where": inner_where or []}}
    if args is not None:
        pred["args"] = args
    if select is not None:
        pred["query"]["select"] = select
    return pred


def q1(where, select=None):
    return {"select": select or ["o.id"], "from": "o", "as": "o", "where": where}


def query(op):
    pred = {"op": op, "args": [{"col": "o.cust"}], "query": {"from": "vip", "as": "v", "select": ["v.cust"]}}
    return {"select": ["o.id"], "from": "orders", "as": "o", "where": [pred]}


class QueryTests(unittest.TestCase):
    def test_in_becomes_semi_join(self):
        self.assertEqual(run(DB, query("in")), {"plan": ["semi_join"], "rows": [[1], [3]]})

    def test_not_in_on_non_null_columns(self):
        self.assertEqual(run(DB, query("not_in")), {"plan": ["anti_join"], "rows": [[2]]})

    def test_no_rewrite_keeps_subplan(self):
        self.assertEqual(run(DB, query("not_in"), rewrite=False), {"plan": ["subplan"], "rows": [[2]]})


class ThreeValuedLogicTests(unittest.TestCase):
    NOT_IN = subquery_pred("not_in", [col("o.k")], ["b.v"])
    IN = subquery_pred("in", [col("o.k")], ["b.v"])
    EXISTS = subquery_pred("exists", None, None, [eq(col("b.v"), col("o.k"))])
    NOT_EXISTS = subquery_pred("not_exists", None, None, [eq(col("b.v"), col("o.k"))])

    def _check(self, db, query_, expected_plan, expected_rows):
        rewritten = run(db, query_, rewrite=True)
        definition = run(db, query_, rewrite=False)
        self.assertEqual(rewritten, {"plan": expected_plan, "rows": expected_rows})
        # The row-by-row 3VL definition must produce exactly the same rows.
        self.assertEqual(definition["rows"], expected_rows)
        self.assertEqual(definition["plan"], ["subplan" if p["op"] in
                         ("in", "not_in", "exists", "not_exists") else "filter"
                         for p in query_["where"]])

    def test_not_in_nullable_both_sides_is_null_aware_and_drops_everything(self):
        # One inner NULL poisons every non-matching outer row; a NULL outer key
        # gives UNKNOWN too, so no row is ever TRUE under NOT IN.
        self._check(DB1, q1([self.NOT_IN]), ["null_aware_anti_join"], [])

    def test_not_in_outer_null_key_is_unknown_even_without_inner_nulls(self):
        pred = subquery_pred("not_in", [col("o.k")], ["b.v"], [is_not_null(col("b.v"))])
        # Inner side is provably non-null, the outer side is not: still null-aware.
        # id 2 has a NULL key -> UNKNOWN -> dropped; ids 4, 5 have no match -> TRUE.
        self._check(DB1, q1([pred]), ["null_aware_anti_join"], [[4], [5]])

    def test_not_in_becomes_anti_join_when_both_sides_provably_non_null(self):
        pred = subquery_pred("not_in", [col("o.k")], ["b.v"], [is_not_null(col("b.v"))])
        query_ = q1([is_not_null(col("o.k")), pred])
        # id 2 removed by the filter; the rest match the hand computation above.
        self._check(DB1, query_, ["filter", "anti_join"], [[4], [5]])

    def test_not_in_over_empty_subquery_is_true_even_for_null_outer_key(self):
        pred = subquery_pred("not_in", [col("o.k")], ["b.v"],
                             [eq(col("b.region"), lit("zz"))])
        self._check(DB1, q1([pred]), ["null_aware_anti_join"],
                    [[1], [2], [3], [4], [5]])

    def test_not_in_empty_subquery_with_non_null_keys_uses_anti_join(self):
        pred = {"op": "not_in", "args": [col("o.cust")],
                "query": {"from": "vip", "as": "v", "select": ["v.cust"],
                          "where": [eq(col("v.cust"), lit(-1))]}}
        query_ = {"select": ["o.id"], "from": "orders", "as": "o", "where": [pred]}
        self._check(DB, query_, ["anti_join"], [[1], [2], [3]])

    def test_not_exists_is_always_a_plain_anti_join(self):
        # Equality on a nullable column: ids 2 and 4 have no row that compares
        # TRUE (NULL = NULL is UNKNOWN), so NOT EXISTS keeps them.
        self._check(DB1, q1([self.NOT_EXISTS]), ["anti_join"], [[2], [4], [5]])

    def test_not_exists_differs_from_not_in_under_nulls(self):
        self._check(DB1, q1([self.NOT_IN]), ["null_aware_anti_join"], [])
        not_exists = run(DB1, q1([self.NOT_EXISTS]), rewrite=True)
        self.assertEqual(not_exists, {"plan": ["anti_join"], "rows": [[2], [4], [5]]})

    def test_exists_is_semi_join_and_ignores_null_comparisons(self):
        self._check(DB1, q1([self.EXISTS]), ["semi_join"], [[1], [3]])

    def test_in_is_semi_join_with_3vl_matching(self):
        self._check(DB1, q1([self.IN]), ["semi_join"], [[1], [3]])

    def test_in_over_empty_subquery_is_false(self):
        pred = subquery_pred("in", [col("o.k")], ["b.v"],
                             [eq(col("b.region"), lit("zz"))])
        self._check(DB1, q1([pred]), ["semi_join"], [])

    def test_not_in_non_null_literal_argument(self):
        inner_nn = [is_not_null(col("b.v"))]
        safe = subquery_pred("not_in", [lit(30)], ["b.v"], inner_nn)
        # Literal 30 is non-null and the inner side is provably non-null.
        self._check(DB1, q1([safe]), ["anti_join"], [[1], [2], [3], [4], [5]])
        poisoned = subquery_pred("not_in", [lit(30)], ["b.v"])
        # Without the inner filter the NULL row makes the result UNKNOWN.
        self._check(DB1, q1([poisoned]), ["null_aware_anti_join"], [])

    def test_not_in_null_literal_argument(self):
        pred = subquery_pred("not_in", [lit(None)], ["b.v"])
        self._check(DB1, q1([pred]), ["null_aware_anti_join"], [])
        empty = subquery_pred("not_in", [lit(None)], ["b.v"],
                              [eq(col("b.region"), lit("zz"))])
        # An empty subquery still makes NOT IN TRUE regardless of the NULL arg.
        self._check(DB1, q1([empty]), ["null_aware_anti_join"],
                    [[1], [2], [3], [4], [5]])

    def test_correlated_not_in_evaluates_nullability_per_outer_row(self):
        pred = subquery_pred("not_in", [col("o.k")], ["b.v"],
                             [eq(col("b.region"), col("o.region"))])
        # id 4: correlation is UNKNOWN for every inner row -> empty set -> TRUE.
        # id 5: the correlated subset is {20}, no NULL row, no match -> TRUE.
        self._check(DB1, q1([pred]), ["null_aware_anti_join"], [[4], [5]])

    def test_correlated_not_exists_is_plain_anti_join(self):
        self._check(DB1, q1([self.NOT_EXISTS]), ["anti_join"], [[2], [4], [5]])
        pred = subquery_pred("not_exists", None, None,
                             [eq(col("b.region"), col("o.region"))])
        # Only id 4 (NULL region) has no region-matching inner row.
        self._check(DB1, q1([pred]), ["anti_join"], [[4]])

    def test_multi_column_in_uses_3vl_not_tuple_equality(self):
        pred = {"op": "in", "args": [col("t.a"), col("t.b")],
                "query": {"from": "s", "as": "s", "select": ["s.x", "s.y"]}}
        query_ = {"select": ["t.a", "t.b"], "from": "t", "as": "t", "where": [pred]}
        # Only (1, 10) compares TRUE; (1, NULL) vs (1, NULL) is UNKNOWN, not TRUE.
        self._check(DB2, query_, ["semi_join"], [[1, 10]])

    def test_multi_column_not_in_is_null_aware(self):
        pred = {"op": "not_in", "args": [col("t.a"), col("t.b")],
                "query": {"from": "s", "as": "s", "select": ["s.x", "s.y"]}}
        query_ = {"select": ["t.a", "t.b"], "from": "t", "as": "t", "where": [pred]}
        # (3,30) compares FALSE with every subquery row -> NOT IN TRUE; every
        # other outer row hits a TRUE match or an UNKNOWN comparison -> dropped.
        self._check(DB2, query_, ["null_aware_anti_join"], [[3, 30]])

    def test_multi_column_not_in_anti_join_after_non_null_proofs(self):
        pred = {"op": "not_in", "args": [col("t.a"), col("t.b")],
                "query": {"from": "s", "as": "s", "select": ["s.x", "s.y"],
                          "where": [is_not_null(col("s.x")), is_not_null(col("s.y"))]}}
        query_ = {"select": ["t.a", "t.b"], "from": "t", "as": "t",
                  "where": [is_not_null(col("t.a")), is_not_null(col("t.b")), pred]}
        # Surviving outer rows: (1,10), (3,30); surviving inner: (1,10), (2,99).
        self._check(DB2, query_, ["filter", "filter", "anti_join"], [[3, 30]])

    def test_multi_column_on_declared_non_null_tables(self):
        not_in = {"op": "not_in", "args": [col("p.a"), col("p.b")],
                  "query": {"from": "q", "as": "q", "select": ["q.x", "q.y"]}}
        query_ = {"select": ["p.a", "p.b"], "from": "p", "as": "p", "where": [not_in]}
        self._check(DB3, query_, ["anti_join"], [[3, 30]])
        in_pred = dict(not_in, op="in")
        query_in = dict(query_, where=[in_pred])
        self._check(DB3, query_in, ["semi_join"], [[1, 10], [2, 20]])

    def test_not_exists_vs_not_in_multi_column(self):
        not_exists = {"op": "not_exists",
                      "query": {"from": "s", "as": "s",
                                "where": [eq(col("s.x"), col("t.a"))]}}
        query_ = {"select": ["t.a", "t.b"], "from": "t", "as": "t", "where": [not_exists]}
        # Rows without an x=a match: (3,30) and the NULL-keyed (NULL,40).
        self._check(DB2, query_, ["anti_join"], [[3, 30], [None, 40]])
        not_in = {"op": "not_in", "args": [col("t.a")],
                  "query": {"from": "s", "as": "s", "select": ["s.x"]}}
        query_ni = dict(query_, where=[not_in])
        # The same tables under NOT IN drop every row: the NULL inner x poisons
        # non-matches and the NULL outer key is UNKNOWN.
        self._check(DB2, query_ni, ["null_aware_anti_join"], [])


class DifferentialFuzzTests(unittest.TestCase):
    """Rewrite vs row-by-row definition over randomized nullable data."""

    DOMAIN = [1, 2, 3]
    OUTER_COLS = ["k1", "k2", "region"]
    INNER_COLS = ["v1", "v2", "region"]

    def _table(self, rng, cols):
        not_null = [c for c in cols if rng.random() < 0.35]
        rows = []
        for _ in range(rng.randrange(0, 6)):
            row = {}
            for c in cols:
                if c in not_null or rng.random() < 0.6:
                    row[c] = rng.choice(self.DOMAIN)
                else:
                    row[c] = None
            rows.append(row)
        return {"not_null": not_null, "rows": rows}

    def _known(self, table, alias, where):
        cols = {f"{alias}.{c}" for c in table["not_null"]}
        for p in where:
            if p.get("op") == "is_not_null" and "col" in p["arg"]:
                cols.add(p["arg"]["col"])
        return cols

    def _provable(self, node, known):
        if "value" in node:
            return node["value"] is not None
        return node.get("col") in known

    def test_rewritten_plans_match_3vl_definition(self):
        rng = random.Random(20261118)
        for _ in range(300):
            db = {"o": self._table(rng, self.OUTER_COLS),
                  "b": self._table(rng, self.INNER_COLS)}
            op = rng.choice(["in", "not_in", "exists", "not_exists"])
            ncol = rng.choice([1, 2])
            inner_where, outer_where = [], []
            if rng.random() < 0.5:
                inner_where.append(eq(col("b.region"), col("o.region")))
            if rng.random() < 0.5:
                inner_where.append(is_not_null(col("b.v1")))
            if ncol == 2 and rng.random() < 0.5:
                inner_where.append(is_not_null(col("b.v2")))
            if rng.random() < 0.3:
                inner_where.append(eq(col("b.v1"), lit(rng.choice(self.DOMAIN))))
            if rng.random() < 0.5:
                outer_where.append(is_not_null(col("o.k1")))
            if ncol == 2 and rng.random() < 0.5:
                outer_where.append(is_not_null(col("o.k2")))

            if op in ("exists", "not_exists"):
                if rng.random() < 0.5:
                    inner_where.append(eq(col("b.v1"), col("o.k1")))
                pred = {"op": op, "query": {"from": "b", "as": "b",
                                            "where": inner_where}}
            else:
                if rng.random() < 0.2:
                    first = lit(None if rng.random() < 0.3
                                else rng.choice(self.DOMAIN))
                else:
                    first = col("o.k1")
                args = [first] + ([col("o.k2")] if ncol == 2 else [])
                select = ["b.v1"] + (["b.v2"] if ncol == 2 else [])
                pred = {"op": op, "args": args,
                        "query": {"from": "b", "as": "b", "select": select,
                                  "where": inner_where}}
            query_ = {"select": ["o.k1"], "from": "o", "as": "o",
                      "where": outer_where + [pred]}

            fast = run(db, query_, rewrite=True)
            slow = run(db, query_, rewrite=False)
            self.assertEqual(fast["rows"], slow["rows"],
                             msg=f"divergence on {op}: {db} / {query_}")

            # Plan shape invariants.
            outer_known = self._known(db["o"], "o", outer_where)
            inner_known = self._known(db["b"], "b", inner_where)
            expected_kinds = ["filter"] * len(outer_where)
            if op in ("in", "exists"):
                expected_kinds.append("semi_join")
            elif op == "not_exists":
                expected_kinds.append("anti_join")
            else:
                outer_safe = all(self._provable(a, outer_known) for a in pred["args"])
                inner_safe = all(c in inner_known for c in pred["query"]["select"])
                expected_kinds.append("anti_join" if outer_safe and inner_safe
                                      else "null_aware_anti_join")
            self.assertEqual(fast["plan"], expected_kinds)
            self.assertEqual(slow["plan"],
                             ["filter"] * len(outer_where) + ["subplan"])


if __name__ == "__main__":
    unittest.main()
