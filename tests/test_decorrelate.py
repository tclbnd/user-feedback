"""Differential tests: the decorrelated plan must agree with row-by-row evaluation.

Every case is run twice -- once planned with the rewrite and once with
``rewrite=False`` -- over several correlation shapes (equality, inequality,
mixed, multi-key, EXISTS, nested) and over data with empty matches, NULLs and
duplicate matches.  The rewritten plan must also stop re-executing subqueries:
``subplan_runs`` has to be 0 and each inner table is read at most once.
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import MultipleRows  # noqa: E402
from query import run  # noqa: E402


# `orders.amount` mixes NULL, duplicate keys and values only some outer rows can
# match; `customers.credit` has NULLs and a value no order exceeds.
SHOP = {
    "customers": {"rows": [
        {"id": 1, "name": "ann", "region": "north", "credit": 100},
        {"id": 2, "name": "bo", "region": "south", "credit": None},
        {"id": 3, "name": "cy", "region": "north", "credit": 300},
        {"id": 4, "name": "dee", "region": "east", "credit": 0},
        {"id": 5, "name": "eve", "region": None, "credit": 50},
    ]},
    "orders": {"rows": [
        {"oid": 1, "cust": 1, "amount": 120, "status": "paid"},
        {"oid": 2, "cust": 1, "amount": 40, "status": "paid"},
        {"oid": 3, "cust": 2, "amount": None, "status": "open"},
        {"oid": 4, "cust": 3, "amount": 90, "status": "paid"},
        {"oid": 5, "cust": 3, "amount": 260, "status": "open"},
        {"oid": 6, "cust": 3, "amount": 310, "status": "paid"},
        {"oid": 7, "cust": 4, "amount": 10, "status": "void"},
        {"oid": 8, "cust": 4, "amount": 10, "status": "void"},     # duplicate match
        {"oid": 9, "cust": None, "amount": 99, "status": "open"},  # NULL key
        {"oid": 10, "cust": 9, "amount": 70, "status": "paid"},    # orphan key
    ]},
    "targets": {"rows": [
        {"region": "north", "goal": 100},
        {"region": "south", "goal": 10},
        {"region": None, "goal": 5},
    ]},
}


def col(name):
    return {"col": name}


def val(x):
    return {"value": x}


def eq(left, right):
    return {"op": "=", "left": left, "right": right}


def sub(agg, column, where, as_="o", table="orders"):
    return {"from": table, "as": as_, "agg": agg, "col": column, "where": where}


def query(select, where=None):
    return {"from": "customers", "as": "c", "select": select, "where": where or []}


def per_customer(agg, column):
    return {"scalar": sub(agg, column, [eq(col("o.cust"), col("c.id"))])}


class Differential(unittest.TestCase):
    def check(self, q, db=SHOP):
        """Both plans must agree; the rewritten one must not rerun subqueries."""
        plain = run(db, q, rewrite=False)
        rewritten = run(db, q, rewrite=True)
        self.assertEqual(rewritten["rows"], plain["rows"])
        for label in rewritten["plan"]:
            self.assertTrue(label.startswith("Decorrelated "), rewritten["plan"])
        self.assertEqual(rewritten["subplan_runs"], 0)
        return rewritten, plain

    def stays_subplan(self, q, plan=None):
        plain = run(SHOP, q, rewrite=False)
        rewritten = run(SHOP, q, rewrite=True)
        self.assertEqual(rewritten["rows"], plain["rows"])
        if plan is not None:
            self.assertEqual(rewritten["plan"], plan)
        self.assertTrue(rewritten["plan"][0].startswith("SubPlan "), rewritten["plan"])
        return rewritten

    def check_error(self, q, db=SHOP):
        with self.assertRaises(MultipleRows):
            run(db, q, rewrite=False)
        with self.assertRaises(MultipleRows):
            run(db, q, rewrite=True)

    # -- empty matches / NULL aggregates --------------------------------
    def test_count_star_empty_match_is_zero(self):
        rewritten, _ = self.check(query([col("c.id"), per_customer("count", None)]))
        self.assertEqual([r[1] for r in rewritten["rows"]], [2, 1, 3, 2, 0])

    def test_count_column_ignores_null(self):
        rewritten, _ = self.check(query([col("c.id"), per_customer("count", "o.amount")]))
        self.assertEqual([r[1] for r in rewritten["rows"]], [2, 0, 3, 2, 0])

    def test_sum_empty_match_is_null_not_zero(self):
        rewritten, _ = self.check(query([col("c.id"), per_customer("sum", "o.amount")]))
        self.assertEqual([r[1] for r in rewritten["rows"]], [160, None, 660, 20, None])

    def test_all_null_aggregate_input_is_null(self):
        rewritten, _ = self.check(query(
            [col("c.id"), per_customer("sum", "o.amount"),
             per_customer("max", "o.amount"), per_customer("min", "o.amount")]))
        self.assertEqual(rewritten["rows"][1], [2, None, None, None])

    # -- correlation shapes ---------------------------------------------
    def test_inequality_inner_gt_outer(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [eq(col("o.cust"), col("c.id")),
                 {"op": ">", "left": col("o.amount"), "right": col("c.credit")}])}]))

    def test_inequality_inner_lt_outer(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "sum", "o.amount",
                [eq(col("o.cust"), col("c.id")),
                 {"op": "<", "left": col("o.amount"), "right": col("c.credit")}])}]))

    def test_inequality_with_flipped_sides(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "max", "o.amount",
                [eq(col("c.id"), col("o.cust")),
                 {"op": ">=", "left": col("c.credit"), "right": col("o.amount")}])}]))

    def test_inequality_without_equality_key(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [{"op": ">=", "left": col("o.amount"), "right": col("c.credit")}])}]))

    def test_not_equal_correlation(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [{"op": "<>", "left": col("o.cust"), "right": col("c.id")}])}]))

    def test_equal_plus_two_inequalities(self):
        self.check(query(
            [col("c.id"),
             {"scalar": sub("count", None,
                            [eq(col("o.cust"), col("c.id")),
                             {"op": ">", "left": col("o.amount"), "right": col("c.credit")}])},
             {"scalar": sub("sum", "o.amount",
                            [eq(col("o.cust"), col("c.id")),
                             {"op": "<=", "left": col("o.amount"), "right": col("c.credit")}])}]))

    def test_uncorrelated_subquery_is_constant(self):
        rewritten, _ = self.check(query(
            [col("c.id"), {"scalar": sub("count", None, [])},
             {"scalar": sub("sum", "o.amount", [])}]))
        self.assertEqual([r[1:] for r in rewritten["rows"]], [[10, 1009]] * 5)

    # -- EXISTS ----------------------------------------------------------
    def test_exists(self):
        rewritten, _ = self.check(query(
            [col("c.id")], [{"op": "exists", "query": sub(None, None,
                                                          [eq(col("o.cust"), col("c.id"))])}]))
        self.assertEqual([r[0] for r in rewritten["rows"]], [1, 2, 3, 4])

    def test_not_exists(self):
        rewritten, _ = self.check(query(
            [col("c.id")], [{"op": "not_exists", "query": sub(None, None,
                                                              [eq(col("o.cust"), col("c.id"))])}]))
        self.assertEqual([r[0] for r in rewritten["rows"]], [5])

    def test_exists_with_inequality(self):
        rewritten, _ = self.check(query(
            [col("c.id")], [{"op": "exists", "query": sub(
                None, None,
                [eq(col("o.cust"), col("c.id")),
                 {"op": ">", "left": col("o.amount"), "right": col("c.credit")}])}]))
        self.assertEqual([r[0] for r in rewritten["rows"]], [1, 3, 4])

    # -- nesting ---------------------------------------------------------
    def test_nested_scalar_inside_correlation(self):
        rewritten, _ = self.check(query(
            [col("c.id"), {"scalar": sub(
                "sum", "o.amount",
                [eq(col("o.cust"), col("c.id")),
                 {"op": ">=", "left": col("o.amount"),
                  "right": {"scalar": sub("max", "t.goal",
                                          [eq(col("t.region"), col("c.region"))],
                                          as_="t", table="targets")}}])}]))
        self.assertEqual(rewritten["plan"], ["Decorrelated o", "Decorrelated t"])

    def test_nested_subquery_in_outer_where(self):
        rewritten, _ = self.check(query(
            [col("c.id")],
            [{"op": ">", "left": {"scalar": sub("sum", "o.amount",
                                                [eq(col("o.cust"), col("c.id"))])},
              "right": val(100)}]))
        self.assertEqual([r[0] for r in rewritten["rows"]], [1, 3])

    # -- duplicate matches and the runtime error -------------------------
    def test_plain_column_duplicate_match_raises(self):
        self.check_error(query([col("c.id"), per_customer(None, "o.amount")]))

    def test_exists_tolerates_duplicate_matches(self):
        self.check(query([col("c.id")], [{"op": "exists", "query": sub(
            None, None, [eq(col("o.cust"), col("c.id"))])}]))

    def test_null_outer_key_matches_nothing(self):
        # customers.region is NULL for eve and targets has a NULL-region goal,
        # but "t.region = c.region" is UNKNOWN for NULL: not a match.
        rewritten, _ = self.check(query(
            [col("c.id"), {"scalar": sub("max", "t.goal",
                                         [eq(col("t.region"), col("c.region"))],
                                         as_="t", table="targets")}]))
        self.assertEqual(rewritten["rows"],
                         [[1, 100], [2, 10], [3, 100], [4, None], [5, None]])

    def test_null_inner_key_never_joins(self):
        self.check(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [{"op": ">", "left": col("o.cust"), "right": col("c.id")}])}]))

    # -- predicates that must keep the SubPlan --------------------------
    def test_or_inside_correlation_stays_subplan(self):
        self.stays_subplan(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [{"op": "or", "args": [eq(col("o.cust"), col("c.id")),
                                       {"op": ">", "left": col("o.amount"),
                                        "right": col("c.credit")}]}])}]))

    def test_is_null_of_outer_alias_stays_subplan(self):
        self.stays_subplan(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [eq(col("o.cust"), col("c.id")),
                 {"op": "is_null", "arg": col("c.credit")}])}]))

    def test_both_sides_correlated_stays_subplan(self):
        # The inner side is a nested subquery reading both o and c, so it is not
        # computable from the inner row alone and the outer subquery stays a
        # SubPlan.  The nested "z" subquery itself is still eligible.
        self.stays_subplan(query(
            [col("c.id"), {"scalar": sub(
                "count", None,
                [{"op": "=", "left": {"scalar": sub(
                    "count", None,
                    [eq(col("z.cust"), col("c.id")),
                     eq(col("z.amount"), col("o.amount"))],
                    as_="z", table="orders")}, "right": val(0)}])}]))

    # -- the plan no longer scans the inner table per outer row ---------
    def test_inner_table_scanned_once(self):
        class Counting(list):
            def __init__(self, rows):
                super().__init__(rows)
                self.reads = 0

            def __iter__(self):
                self.reads += 1
                return super().__iter__()

        q = query([col("c.id"), per_customer("sum", "o.amount")])
        db = {"customers": SHOP["customers"],
              "orders": {"rows": Counting(SHOP["orders"]["rows"])}}
        out = run(db, q, rewrite=True)
        self.assertEqual(out["subplan_runs"], 0)
        self.assertEqual(db["orders"]["rows"].reads, 1)

        db = {"customers": SHOP["customers"],
              "orders": {"rows": Counting(SHOP["orders"]["rows"])}}
        run(db, q, rewrite=False)
        self.assertEqual(db["orders"]["rows"].reads, 5)  # once per outer row


class RandomDifferential(unittest.TestCase):
    """Random correlated subqueries over random data, rewritten vs row-by-row."""

    OUTER_ROWS = [
        {"id": 1, "region": "north", "credit": 100},
        {"id": 2, "region": "south", "credit": None},
        {"id": 3, "region": "north", "credit": 300},
        {"id": 4, "region": None, "credit": 0},
        {"id": 5, "region": "east", "credit": 50},
    ]
    OPS = ["=", "<>", "<", "<=", ">", ">="]

    def build_data(self, rng):
        orders = []
        for oid in range(rng.randint(0, 14)):
            orders.append({"oid": oid,
                           "cust": rng.choice([None, 1, 2, 3, 4, 5, 6, 9]),
                           "amount": rng.choice([None, 0, 10, 40, 90, 120, 260, 310]),
                           "qty": rng.choice([None, 0, 1, 2, 3]),
                           "status": rng.choice(["paid", "open", None])})
        targets = [{"region": r, "goal": rng.choice([None, 5, 10, 100])}
                   for r in ("north", "south", None)]
        return {"customers": {"rows": self.OUTER_ROWS},
                "orders": {"rows": orders}, "targets": {"rows": targets}}

    def random_subquery(self, rng, depth=0):
        conjuncts = []
        for _ in range(rng.randint(0, 3)):
            style = rng.random()
            left_inner = rng.choice(["o.cust", "o.amount", "o.qty"])
            right_outer = rng.choice(["c.id", "c.credit"])
            op = rng.choice(self.OPS)
            if style < 0.55:
                conjuncts.append(eq(col(left_inner), col(right_outer)))
            elif style < 0.80:
                if rng.random() < 0.5:
                    conjuncts.append({"op": op, "left": col(left_inner),
                                      "right": col(right_outer)})
                else:
                    conjuncts.append({"op": op, "left": col(right_outer),
                                      "right": col(left_inner)})
            elif style < 0.86:
                # Second equality key: a composite correlation.
                conjuncts.append(eq(col("o.qty"), col("c.id")))
            elif style < 0.92:
                conjuncts.append(eq(col("o.status"), val("paid")))
            elif style < 0.96 and depth == 0:
                conjuncts.append({"op": rng.choice(self.OPS), "left": col("o.amount"),
                                  "right": {"scalar": sub(
                                      rng.choice(["max", "min", "sum", "count"]),
                                      "t.goal", [eq(col("t.region"), col("c.region"))],
                                      as_="t", table="targets")}})
            elif style < 0.98:
                # Not safely rewritable: an enclosing alias inside OR.
                conjuncts.append({"op": "or", "args": [
                    eq(col("o.cust"), col("c.id")),
                    {"op": ">", "left": col("o.amount"), "right": col("c.credit")}]})
            else:
                # Not safely rewritable: an enclosing alias inside IS NULL.
                conjuncts.append({"op": "is_null", "arg": col("c.credit")})
        agg = rng.choice(["count", "sum", "min", "max", None])
        column = None if agg == "count" and rng.random() < 0.5 else "o.amount"
        return {"scalar": sub(agg, column, conjuncts)}

    def test_random_correlations(self):
        rng = random.Random(20261118)
        for case in range(500):
            db = self.build_data(rng)
            select = [col("c.id"), self.random_subquery(rng)]
            if rng.random() < 0.5:
                select.append(self.random_subquery(rng))
            where = []
            if rng.random() < 0.4:
                where.append({"op": rng.choice(["exists", "not_exists"]),
                              "query": sub(None, None,
                                           [eq(col("o.cust"), col("c.id")),
                                            eq(col("o.qty"), col("c.id"))])})
            q = query(select, where)
            with self.subTest(case=case):
                self.assert_same(db, q)

    def assert_same(self, db, q):
        def outcome(rewrite):
            try:
                return "ok", run(db, q, rewrite=rewrite)["rows"]
            except MultipleRows as exc:
                return "error", str(exc)

        plain = outcome(False)
        rewritten = outcome(True)
        self.assertEqual(rewritten, plain)


class MixedTypeDifferential(unittest.TestCase):
    """Values that cannot be compared or sorted must behave as row-by-row does."""

    def test_incomparable_values(self):
        db = {"customers": {"rows": [{"id": 1, "v": 10}, {"id": 2, "v": "x"},
                                     {"id": 3, "v": None}]},
              "orders": {"rows": [{"k": 1, "v": 5}, {"k": 1, "v": "y"},
                                  {"k": 2, "v": 5}, {"k": 2, "v": 7},
                                  {"k": 3, "v": None}]}}
        for op in ["=", "<>", "<", "<=", ">", ">="]:
            for flipped in (False, True):
                inner = {"op": op, "left": col("o.v"), "right": col("c.v")}
                if flipped:
                    inner = {"op": op, "left": col("c.v"), "right": col("o.v")}
                q = query([col("c.id"), {"scalar": sub(
                    "count", None, [eq(col("o.k"), col("c.id")), inner])}])
                with self.subTest(op=op, flipped=flipped):
                    def outcome(rewrite):
                        try:
                            return "ok", run(db, q, rewrite=rewrite)["rows"]
                        except TypeError:
                            return "type-error", None
                    self.assertEqual(outcome(True), outcome(False))

    def test_unmatched_group_with_incomparable_values_is_not_folded(self):
        # Group k=2 holds 5 and "y"; no outer row can reach it because
        # customers.id is never 2.  Row-by-row never aggregates it, so the
        # rewritten plan must not raise either.
        db = {"customers": {"rows": [{"id": 1, "v": 5}, {"id": 3, "v": 6}]},
              "orders": {"rows": [{"k": 1, "v": 5}, {"k": 2, "v": 5},
                                  {"k": 2, "v": "y"}, {"k": 3, "v": 7}]}}
        for agg in ["sum", "min", "max"]:
            q = query([col("c.id"), {"scalar": sub(agg, "o.v", [eq(col("o.k"), col("c.id"))])}])
            with self.subTest(agg=agg):
                self.assertEqual(run(db, q, rewrite=True)["rows"],
                                 run(db, q, rewrite=False)["rows"])

    def test_matched_group_with_incomparable_values_raises_in_both_plans(self):
        db = {"customers": {"rows": [{"id": 1}, {"id": 2}]},
              "orders": {"rows": [{"k": 1, "v": 5}, {"k": 2, "v": 5},
                                  {"k": 2, "v": "y"}]}}
        q = query([col("c.id"), {"scalar": sub("sum", "o.v", [eq(col("o.k"), col("c.id"))])}])
        for rewrite in (False, True):
            with self.assertRaises(TypeError):
                run(db, q, rewrite=rewrite)


if __name__ == "__main__":
    unittest.main()

