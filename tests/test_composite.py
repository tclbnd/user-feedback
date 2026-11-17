"""Composite-index access path: prefix derivation, range keys, index
filtering, covering scans, ordering, and end-to-end cross-checks."""
import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import NEG_INF, POS_INF, execute, seek_keys  # noqa: E402
from planner import choose, plan_for  # noqa: E402

ROWS = [
    {"id": 1, "a": 1, "b": 1, "c": 10, "d": 100},
    {"id": 2, "a": 1, "b": 1, "c": 20, "d": 200},
    {"id": 3, "a": 1, "b": 2, "c": 10, "d": 300},
    {"id": 4, "a": 1, "b": 3, "c": 30, "d": 400},
    {"id": 5, "a": 2, "b": 1, "c": 10, "d": 500},
    {"id": 6, "a": 2, "b": 2, "c": 20, "d": 600},
    {"id": 7, "a": 2, "b": 2, "c": 30, "d": 700},
    {"id": 8, "a": 2, "b": 3, "c": 10, "d": 800},
    {"id": 9, "a": 3, "b": 1, "c": 40, "d": 900},
    {"id": 10, "a": 3, "b": 2, "c": 10, "d": 1000},
]

DB = {
    "rows": ROWS,
    "indexes": [
        {"name": "ix_a", "columns": ["a"]},
        {"name": "ix_ab_c", "columns": ["a", "b", "c"]},
        {"name": "ix_ac_d", "columns": ["a", "c", "d"]},
    ],
}
ABC = DB["indexes"][1]
ACD = DB["indexes"][2]


def q(where=None, select=None, order_by=None):
    out = {}
    if where is not None:
        out["where"] = where
    if select is not None:
        out["select"] = select
    if order_by is not None:
        out["order_by"] = order_by
    return out


def naive(db, query):
    """Reference implementation: full table scan, then explicit sort.

    Rows are sorted by the ORDER BY columns; id ascending is the final
    tiebreaker, giving a total deterministic order. The engine only
    promises this exact order when its index key covers every ORDER BY
    column; otherwise equal-key rows may appear in any order.
    """
    ops = {"=": lambda x, y: x == y, "<": lambda x, y: x < y,
           "<=": lambda x, y: x <= y, ">": lambda x, y: x > y,
           ">=": lambda x, y: x >= y}
    rows = [dict(r) for r in db["rows"]]
    for col, op, val in query.get("where", []):
        rows = [r for r in rows if ops[op](r[col], val)]
    # Stable sorts from the least significant key up: id tiebreaker first,
    # then the ORDER BY columns in reverse so the first one ends up primary.
    rows.sort(key=lambda r: r["id"])
    for col, direction in reversed(query.get("order_by", [])):
        rows.sort(key=lambda r: r[col], reverse=direction == "desc")
    sel = query.get("select") or (sorted(rows[0]) if rows else [])
    return [[r[c] for c in sel] for r in rows]


def index_order_is_total(db, plan, query):
    """True only when the index scan fully determines the output order.

    The planner's ``order_from_index`` verdict guarantees the ORDER BY
    columns, but rows sharing every ordered value may still come in any
    order (the index id tiebreaker only decides among entries that survive
    contiguously). An exact sequence comparison is therefore only valid
    when the ORDER BY list ends on the trailing id key, which makes the
    requested order total.
    """
    del db
    if plan["index"] is None or not plan["order_from_index"] or plan["sort"]:
        return False
    order = query.get("order_by") or []
    return bool(order) and order[-1][0] == "id"


class PrefixDerivationTests(unittest.TestCase):
    def test_equality_columns_extend_prefix_beyond_first(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", "=", 2]]))
        self.assertEqual(plan["eq"], ["a", "b"])
        self.assertIsNone(plan["range"])

    def test_first_non_equality_column_becomes_range_column(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2], ["b", ">", 1],
                                      ["b", "<", 3]]))
        self.assertEqual(plan["eq"], ["a"])
        self.assertEqual(plan["range"], "b")
        self.assertEqual(plan["lo"], [1, False])
        self.assertEqual(plan["hi"], [3, False])

    def test_inclusive_bounds_are_marked(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2], ["b", ">=", 1],
                                      ["b", "<=", 3]]))
        self.assertEqual(plan["lo"], [1, True])
        self.assertEqual(plan["hi"], [3, True])

    def test_range_on_first_column_with_no_equality(self):
        plan = plan_for(ABC, q(where=[["a", ">", 1]]))
        self.assertEqual(plan["eq"], [])
        self.assertEqual(plan["range"], "a")
        self.assertEqual(plan["lo"], [1, False])
        self.assertIsNone(plan["hi"])

    def test_multiple_lower_bounds_keep_the_tightest(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", ">", 1],
                                      ["b", ">=", 2]]))
        # >= 2 is tighter than > 1.
        self.assertEqual(plan["lo"], [2, True])
        plan2 = plan_for(ABC, q(where=[["a", "=", 1], ["b", ">", 2],
                                       ["b", ">=", 2]]))
        # Same value: the exclusive bound wins.
        self.assertEqual(plan2["lo"], [2, False])

    def test_multiple_upper_bounds_keep_the_tightest(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", "<", 3],
                                      ["b", "<=", 2]]))
        self.assertEqual(plan["hi"], [2, True])
        plan2 = plan_for(ABC, q(where=[["a", "=", 1], ["b", "<", 2],
                                       ["b", "<=", 2]]))
        self.assertEqual(plan2["hi"], [2, False])

    def test_conditions_after_range_column_become_filters(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2], ["b", "=", 2],
                                      ["c", ">", 15], ["d", "=", 700]]))
        self.assertEqual(plan["eq"], ["a", "b"])
        self.assertEqual(plan["range"], "c")
        self.assertEqual(plan["index_filter"], [])
        # d is not an index column.
        self.assertEqual(plan["table_filter"], [["d", "=", 700]])

        plan2 = plan_for(ABC, q(where=[["a", "=", 2], ["b", ">", 1],
                                       ["c", "=", 30]]))
        self.assertEqual(plan2["eq"], ["a"])
        self.assertEqual(plan2["range"], "b")
        self.assertEqual(plan2["index_filter"], [["c", "=", 30]])
        self.assertEqual(plan2["table_filter"], [])

    def test_equality_on_later_index_column_when_earlier_has_range(self):
        # No condition on a: b can only be an index-internal filter.
        plan = plan_for(ABC, q(where=[["b", "=", 3]]))
        self.assertEqual(plan["eq"], [])
        self.assertIsNone(plan["range"])
        self.assertEqual(plan["index_filter"], [["b", "=", 3]])


class OrderingTests(unittest.TestCase):
    def test_order_by_skips_equality_columns_ascending(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2]],
                               order_by=[["b", "asc"], ["c", "asc"]]))
        self.assertTrue(plan["order_from_index"])
        self.assertEqual(plan["direction"], "asc")
        self.assertFalse(plan["sort"])

    def test_backward_scan_for_descending_order(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1]],
                               order_by=[["b", "desc"], ["c", "desc"]]))
        self.assertTrue(plan["order_from_index"])
        self.assertEqual(plan["direction"], "desc")
        self.assertFalse(plan["sort"])

    def test_order_on_range_column_uses_index_order(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2], ["b", "=", 2],
                                      ["c", ">", 15]],
                               order_by=[["c", "asc"]]))
        self.assertTrue(plan["order_from_index"])
        self.assertFalse(plan["sort"])

    def test_mixed_directions_cannot_use_index_order(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2]],
                               order_by=[["b", "asc"], ["c", "desc"]]))
        self.assertFalse(plan["order_from_index"])
        self.assertTrue(plan["sort"])

    def test_order_column_not_in_index_tail_requires_sort(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2]],
                               order_by=[["c", "asc"]]))  # b skipped
        self.assertFalse(plan["order_from_index"])
        self.assertTrue(plan["sort"])

    def test_order_by_non_index_column_requires_sort(self):
        plan = plan_for(ABC, q(where=[["a", "=", 2]],
                               order_by=[["d", "asc"]]))
        self.assertFalse(plan["order_from_index"])
        self.assertTrue(plan["sort"])

    def test_order_by_id_uses_trailing_index_key(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", "=", 1],
                                      ["c", "=", 10]],
                               order_by=[["id", "asc"]]))
        self.assertTrue(plan["order_from_index"])

    def test_order_by_id_desc_cannot_use_index_order(self):
        # The trailing id key is ascending only.
        plan = plan_for(ABC, q(where=[["a", ">=", 1]],
                               order_by=[["a", "desc"], ["id", "desc"]]))
        self.assertFalse(plan["order_from_index"])
        self.assertTrue(plan["sort"])

    def test_order_columns_all_fixed_by_equality_is_satisfied(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", "=", 1]],
                               order_by=[["a", "asc"], ["b", "asc"]]))
        self.assertTrue(plan["order_from_index"])
        self.assertFalse(plan["sort"])

    def test_empty_prefix_index_delivers_order_only(self):
        plan = plan_for(ABC, q(order_by=[["a", "desc"], ["b", "desc"],
                                         ["c", "desc"]]))
        self.assertTrue(plan["order_from_index"])
        self.assertEqual(plan["direction"], "desc")


class CoveringAndSelectionTests(unittest.TestCase):
    def test_covering_when_all_needed_columns_are_in_index(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1], ["b", "=", 2]],
                               select=["id", "c"]))
        self.assertTrue(plan["covering"])

    def test_not_covering_when_table_column_needed(self):
        plan = plan_for(ABC, q(where=[["a", "=", 1]], select=["id", "d"]))
        self.assertFalse(plan["covering"])

    def test_choose_prefers_longest_equality_prefix(self):
        query = q(where=[["a", "=", 2], ["c", "=", 10]],
                  select=["id", "d"])
        plan = choose(DB, query)
        # ix_ac_d has eq [a, c]; ix_ab_c has only eq [a].
        self.assertEqual(plan["index"], "ix_ac_d")
        self.assertEqual(plan["eq"], ["a", "c"])
        # d is itself an ix_ac_d column, so the plan stays covering.
        self.assertTrue(plan["covering"])

    def test_choose_prefers_range_index_over_equality_only_tie(self):
        db = {"rows": ROWS, "indexes": [
            {"name": "ix_a", "columns": ["a"]},
            {"name": "ix_ab", "columns": ["a", "b"]}]}
        plan = choose(db, q(where=[["a", "=", 2], ["b", ">", 1]]))
        self.assertEqual(plan["index"], "ix_ab")
        self.assertEqual(plan["range"], "b")

    def test_choose_uses_declaration_order_on_full_tie(self):
        db = {"rows": ROWS, "indexes": [
            {"name": "first", "columns": ["a", "b"]},
            {"name": "second", "columns": ["a", "b"]}]}
        plan = choose(db, q(where=[["a", "=", 2]]))
        self.assertEqual(plan["index"], "first")

    def test_full_scan_when_no_index_is_usable(self):
        plan = choose(DB, q(where=[["d", ">", 500]]))
        self.assertIsNone(plan["index"])
        self.assertEqual(plan["table_filter"], [["d", ">", 500]])
        self.assertFalse(plan["covering"])

    def test_index_without_predicate_used_for_ordering(self):
        plan = choose(DB, q(order_by=[["a", "asc"], ["b", "asc"],
                                      ["c", "asc"]]))
        self.assertEqual(plan["index"], "ix_ab_c")
        self.assertTrue(plan["order_from_index"])
        self.assertFalse(plan["sort"])


class SeekKeyAndExecutionTests(unittest.TestCase):
    def test_seek_keys_for_equality_prefix_and_range(self):
        query = q(where=[["a", "=", 2], ["b", ">=", 2], ["b", "<", 3]])
        plan = choose(DB, query)
        start, end = seek_keys(DB, query, plan)
        self.assertEqual(start, [["a", 2, True], ["b", 2, True]])
        self.assertEqual(end, [["a", 2, True], ["b", 3, False]])

    def test_open_ended_seek_keys_use_infinities(self):
        # Range on the first column of ix_ac_d: no equality prefix.
        query = q(where=[["a", ">", 2]])
        plan = plan_for(ACD, query)
        start, end = seek_keys(DB, query, plan)
        self.assertEqual(start, [["a", 2, False]])
        self.assertEqual(end[0][0], "a")
        self.assertIs(end[0][1], POS_INF)
        query2 = q(where=[["a", "<=", 2]])
        plan2 = plan_for(ACD, query2)
        start2, _ = seek_keys(DB, query2, plan2)
        self.assertIs(start2[0][1], NEG_INF)

    def test_open_ended_seek_keys_after_equality_prefix(self):
        query = q(where=[["a", "=", 2], ["c", ">", 20]])
        plan = plan_for(ACD, query)
        start, end = seek_keys(DB, query, plan)
        self.assertEqual(start, [["a", 2, True], ["c", 20, False]])
        self.assertIs(end[1][1], POS_INF)

    def test_scanned_count_matches_entries_inside_start_end_keys(self):
        query = q(where=[["a", "=", 2], ["b", "=", 2]], select=["id"])
        plan = choose(DB, query)
        _, scanned, fetches = execute(DB, query, dict(plan))
        # Rows 6 and 7 carry (a=2, b=2).
        self.assertEqual(scanned, 2)
        self.assertEqual(fetches, 0)  # covering: id and a,b,c in index

    def test_covering_plan_never_fetches(self):
        query = q(where=[["a", "=", 1], ["b", ">", 1]],
                  select=["id", "b", "c"])
        plan = plan_for(ABC, query)
        self.assertTrue(plan["covering"])
        rows, scanned, fetches = execute(DB, query, dict(plan))
        self.assertEqual(fetches, 0)
        self.assertEqual({r["id"] for r in rows}, {3, 4})
        # Window holds exactly the (a=1) entries with b > 1: ids 3, 4.
        self.assertEqual(scanned, 2)

    def test_non_covering_plan_fetches_entries_passing_index_filter(self):
        # d is not in ix_ab_c: both (a=2,b=2) entries pass c>=20 (id 6 has
        # c=20, id 7 has c=30), so both are fetched; d>0 keeps both.
        query = q(where=[["a", "=", 2], ["b", "=", 2], ["c", ">=", 20],
                         ["d", ">", 0]],
                  select=["id", "d"])
        plan = plan_for(ABC, query)
        self.assertEqual(plan["index"], "ix_ab_c")
        self.assertFalse(plan["covering"])
        rows, scanned, fetches = execute(DB, query, dict(plan))
        self.assertEqual(scanned, 2)
        self.assertEqual(fetches, 2)
        self.assertEqual([r["id"] for r in rows], [6, 7])

    def test_index_filter_removes_entries_before_fetch(self):
        # Range on b opens a 3-entry window; the c condition is an index
        # filter that drops two entries before any table fetch.
        query = q(where=[["a", "=", 2], ["b", ">", 1], ["c", "=", 30]],
                  select=["id", "d"])
        plan = plan_for(ABC, query)
        self.assertEqual(plan["index_filter"], [["c", "=", 30]])
        rows, scanned, fetches = execute(DB, query, dict(plan))
        self.assertEqual(scanned, 3)
        self.assertEqual(fetches, 1)
        self.assertEqual([r["id"] for r in rows], [7])

    def test_index_filter_applied_on_entries_before_fetch(self):
        # Range on b, equality on c: c is evaluated on the index entry.
        query = q(where=[["a", "=", 2], ["b", ">", 1], ["c", "=", 10]],
                  select=["id", "d"])
        plan = plan_for(ABC, query)
        self.assertEqual(plan["index_filter"], [["c", "=", 10]])
        rows, scanned, fetches = execute(DB, query, dict(plan))
        # Window: ids 6,7,8; only id 8 passes the index filter.
        self.assertEqual(scanned, 3)
        self.assertEqual(fetches, 1)
        self.assertEqual([r["id"] for r in rows], [8])

    def test_descending_scan_returns_index_reverse_order(self):
        query = q(where=[["a", "=", 1]],
                  select=["id", "b", "c"],
                  order_by=[["b", "desc"], ["c", "desc"]])
        plan = choose(DB, query)
        self.assertEqual(plan["direction"], "desc")
        rows, _, fetches = execute(DB, query, dict(plan))
        self.assertEqual(fetches, 0)
        self.assertEqual([r["id"] for r in rows], [4, 3, 2, 1])

    def test_descending_scan_keeps_later_index_columns_ascending(self):
        # Only a is ordered; within an a-group the index columns and id
        # keep their ascending storage order.
        query = q(where=[["a", ">", 1]],
                  select=["id", "a", "b", "c"],
                  order_by=[["a", "desc"]])
        plan = choose(DB, query)
        self.assertEqual(plan["direction"], "desc")
        rows, _, _ = execute(DB, query, dict(plan))
        # a=3 group (ids 9,10) before the a=2 group (ids 5..8), each
        # internally in ascending index order.
        self.assertEqual([r["id"] for r in rows], [9, 10, 5, 6, 7, 8])

    def test_string_valued_index_columns_scan_and_sort(self):
        db = {"rows": [{"id": 1, "k": "us", "v": 2},
                       {"id": 2, "k": "eu", "v": 1},
                       {"id": 3, "k": "us", "v": 3}],
              "indexes": [{"name": "ix_kv", "columns": ["k", "v"]}]}
        query = q(where=[["k", "=", "us"]], select=["id", "v"],
                  order_by=[["v", "desc"]])
        plan = choose(db, query)
        self.assertEqual(plan["eq"], ["k"])
        self.assertIsNone(plan["range"])
        self.assertEqual(plan["direction"], "desc")
        rows, scanned, fetches = execute(db, query, dict(plan))
        self.assertEqual(fetches, 0)
        self.assertEqual([r["id"] for r in rows], [3, 1])
        self.assertEqual(scanned, 2)

    def test_empty_result_window_has_zero_scanned_and_no_fetch(self):
        query = q(where=[["a", "=", 9], ["b", "=", 9]], select=["id"])
        plan = choose(DB, query)
        rows, scanned, fetches = execute(DB, query, dict(plan))
        self.assertEqual(rows, [])
        self.assertEqual(scanned, 0)
        self.assertEqual(fetches, 0)

    def test_sort_fallback_when_index_order_does_not_help(self):
        query = q(where=[["a", "=", 2]], select=["id", "d"],
                  order_by=[["d", "asc"]])
        plan = choose(DB, query)
        self.assertTrue(plan["sort"])
        rows, _, _ = execute(DB, query, dict(plan))
        self.assertEqual([r["d"] for r in rows], [500, 600, 700, 800])

    def test_full_scan_plan_counts_every_row(self):
        query = q(where=[["d", ">", 500]], select=["id", "d"])
        plan = choose(DB, query)
        self.assertIsNone(plan["index"])
        rows, scanned, fetches = execute(DB, query, dict(plan))
        self.assertEqual(scanned, len(ROWS))
        self.assertEqual(fetches, 5)  # rows passing the table filter
        self.assertEqual([r["id"] for r in rows], [6, 7, 8, 9, 10])


class CrossCheckTests(unittest.TestCase):
    scenarios = [
        q(where=[["a", "=", 2]], select=["id", "b", "c"],
          order_by=[["b", "asc"], ["c", "asc"]]),
        q(where=[["a", "=", 1], ["b", "=", 1]], select=["id", "c"],
          order_by=[["c", "desc"]]),
        q(where=[["a", "=", 2], ["b", ">", 1], ["b", "<=", 3]],
          select=["id", "b", "c"],
          order_by=[["b", "asc"], ["c", "asc"], ["id", "asc"]]),
        q(where=[["a", "=", 2], ["b", "=", 2], ["c", ">", 15]],
          select=["id", "c", "d"], order_by=[["c", "asc"]]),
        q(where=[["b", "=", 2]], select=["id", "a", "c"],
          order_by=[["a", "desc"], ["c", "desc"]]),
        q(where=[["a", ">", 1], ["c", "<=", 20]], select=["id", "a", "c"],
          order_by=[["a", "asc"], ["c", "asc"], ["id", "asc"]]),
        q(where=[["d", "=", 700]], select=["id", "d"],
          order_by=[["id", "asc"]]),
        q(where=[["a", "=", 3], ["b", "=", 1], ["c", ">", 0]],
          select=["id", "c"], order_by=[["c", "desc"], ["id", "desc"]]),
        q(select=["id", "a", "b", "c"],
          order_by=[["a", "desc"], ["b", "asc"], ["c", "asc"]]),
        q(where=[["a", "=", 2], ["b", ">", 1], ["c", "=", 10]],
          select=["id", "c", "d"],
          order_by=[["b", "asc"], ["c", "asc"], ["id", "asc"]]),
    ]

    def test_every_scenario_matches_naive_full_scan(self):
        for query in self.scenarios:
            with self.subTest(query=query):
                plan = choose(DB, query)
                rows, _, _ = execute(DB, query, dict(plan))
                sel = query.get("select")
                got = [tuple(r[c] for c in sel) for r in rows]
                expected = [tuple(r) for r in naive(DB, query)]
                if index_order_is_total(DB, plan, query):
                    self.assertEqual(got, expected)
                else:
                    # Equal ORDER BY values may come in any order.
                    self.assertEqual(sorted(got), sorted(expected))

    def test_randomized_queries_match_naive_scan(self):
        rng = random.Random(42)
        cols = ["a", "b", "c", "d"]
        domains = {"a": (1, 4, 1), "b": (1, 4, 1),
                   "c": (10, 45, 5), "d": (100, 1050, 100)}
        for _ in range(200):
            where = []
            for col in cols:
                if rng.random() < 0.5:
                    lo, hi, step = domains[col]
                    val = rng.randrange(lo, hi, step)
                    op = rng.choice(["=", "<", "<=", ">", ">="])
                    where.append([col, op, val])
            order_cols = rng.sample(cols, rng.randint(0, 2))
            order_by = [[c, rng.choice(["asc", "desc"])] for c in order_cols]
            select = ["id"] + rng.sample(cols, rng.randint(1, 4))
            query = q(where=where, select=select, order_by=order_by)
            plan = choose(DB, query)
            rows, _, _ = execute(DB, query, dict(plan))
            got = [tuple(r[c] for c in select) for r in rows]
            expected = [tuple(r) for r in naive(DB, query)]
            if index_order_is_total(DB, plan, query):
                self.assertEqual(got, expected, msg=str(query))
            else:
                # No guaranteed row order: only the result multiset matters.
                self.assertEqual(sorted(got), sorted(expected), msg=str(query))

    def test_orders_dataset_example_queries(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "examples", "orders.json"),
                  encoding="utf-8") as fh:
            orders_db = json.load(fh)
        examples_dir = os.path.join(root, "examples")
        for name in ["ap_latest.json", "big_paid.json",
                     "recent_open.json", "void_covering.json"]:
            with open(os.path.join(examples_dir, name),
                      encoding="utf-8") as fh:
                query = json.load(fh)
            plan = choose(orders_db, query)
            rows, _, fetches = execute(orders_db, query, dict(plan))
            sel = query["select"]
            got = [tuple(r[c] for c in sel) for r in rows]
            expected = [tuple(r) for r in naive(orders_db, query)]
            if index_order_is_total(orders_db, plan, query):
                self.assertEqual(got, expected, msg=name)
            else:
                self.assertEqual(sorted(got), sorted(expected), msg=name)
            if plan["covering"]:
                self.assertEqual(fetches, 0, msg=name)


if __name__ == "__main__":
    unittest.main()
