import unittest

from gsets.aggregate import grouping, run
from gsets.clauses import expand

ROWS = [
    {"region": "eu", "year": 2025, "quarter": 1, "amount": 10},
    {"region": "eu", "year": 2025, "quarter": 2, "amount": 20},
    {"region": "eu", "year": 2026, "quarter": 1, "amount": 5},
    {"region": "us", "year": 2025, "quarter": 1, "amount": 7},
    {"region": "us", "year": None, "quarter": None, "amount": 3},
]


def _index(out, key_cols, label):
    return {tuple(r[c] for c in key_cols) + (r[label],): r for r in out}


class ExpansionTests(unittest.TestCase):
    def test_rollup_preserves_clause_order(self):
        self.assertEqual(
            expand([{"rollup": ["year", "quarter"]}]),
            [["year", "quarter"], ["year"], []],
        )

    def test_rollup_with_composite_items(self):
        self.assertEqual(
            expand([{"rollup": [["a", "b"], "c"]}]),
            [["a", "b", "c"], ["a", "b"], []],
        )

    def test_cube_expansion(self):
        self.assertEqual(
            expand([{"cube": ["a", "b"]}]),
            [["a", "b"], ["a"], ["b"], []],
        )

    def test_plain_column_cross_rollup(self):
        self.assertEqual(
            expand(["region", {"rollup": ["year", "quarter"]}]),
            [
                ["region", "year", "quarter"],
                ["region", "year"],
                ["region"],
            ],
        )

    def test_explicit_grouping_sets(self):
        self.assertEqual(
            expand([{"sets": [["a", "b"], ["a"], []]}]),
            [["a", "b"], ["a"], []],
        )

    def test_all_keeps_duplicate_grouping_sets(self):
        self.assertEqual(
            expand([{"sets": [["a"], ["a"], ["b"]]}]),
            [["a"], ["a"], ["b"]],
        )

    def test_distinct_drops_duplicate_grouping_sets(self):
        self.assertEqual(
            expand([{"sets": [["a"], ["a"], ["b"]]}], distinct=True),
            [["a"], ["b"]],
        )

    def test_distinct_equivalent_sets_from_cross_product(self):
        # The empty set appears twice (rollup's () x sets' ()), plus the
        # others; DISTINCT collapses the repeated () while ALL keeps it.
        q = [{"rollup": ["a"]}, {"sets": [[], ["b"]]}]
        self.assertEqual(
            expand(q),
            [["a"], ["a", "b"], [], ["b"]],
        )
        self.assertEqual(
            expand(q, distinct=True),
            [["a"], ["a", "b"], [], ["b"]],
        )

    def test_duplicate_column_inside_one_set_collapsed(self):
        self.assertEqual(expand([{"sets": [["a", "a", "b"]]}]), [["a", "b"]])

    def test_empty_group_by(self):
        self.assertEqual(expand([]), [[]])


class GroupingBitmaskTests(unittest.TestCase):
    def test_single_argument(self):
        self.assertEqual(grouping(["a"], ["a"]), 0)
        self.assertEqual(grouping([], ["a"]), 1)

    def test_rightmost_argument_is_lowest_bit(self):
        cols = ["year", "quarter"]
        self.assertEqual(grouping(["year", "quarter"], cols), 0)
        # quarter aggregated away: bit 0 -> 1
        self.assertEqual(grouping(["year"], cols), 1)
        # year aggregated away: bit 1 -> 2
        self.assertEqual(grouping(["quarter"], cols), 2)
        # grand total: both bits -> 3
        self.assertEqual(grouping([], cols), 3)

    def test_bit_order_follows_argument_order(self):
        # quarter listed first: it owns the higher bit
        self.assertEqual(grouping(["year"], ["quarter", "year"]), 2)
        self.assertEqual(grouping(["quarter"], ["quarter", "year"]), 1)


class AggregationTests(unittest.TestCase):
    def test_rollup_rows_and_grouping_values(self):
        select = {
            "cols": ["year", "quarter"],
            "aggs": [["sum", "amount"], ["count_star", None]],
            "grouping": [["year", "quarter"]],
        }
        out = run(ROWS, select, expand([{"rollup": ["year", "quarter"]}]))
        label = "grouping(year,quarter)"
        g = _index(out, ["year", "quarter"], label)
        # detail rows, including the genuinely-null (NULL, NULL) group
        self.assertEqual(g[(2025, 1, 0)]["sum(amount)"], 17)
        self.assertEqual(g[(2025, 1, 0)]["count_star(*)"], 2)
        self.assertEqual(g[(2025, 2, 0)]["sum(amount)"], 20)
        self.assertEqual(g[(2026, 1, 0)]["sum(amount)"], 5)
        self.assertEqual(g[(None, None, 0)]["sum(amount)"], 3)
        # year subtotals: grouping = 1 (quarter bit only)
        self.assertEqual(g[(2025, None, 1)]["sum(amount)"], 37)
        self.assertEqual(g[(2026, None, 1)]["sum(amount)"], 5)
        self.assertEqual(g[(None, None, 1)]["sum(amount)"], 3)
        # grand total: grouping = 3
        self.assertEqual(g[(None, None, 3)]["sum(amount)"], 45)
        self.assertEqual(g[(None, None, 3)]["count_star(*)"], 5)
        self.assertEqual(len(out), 8)

    def test_mixed_plain_and_rollup(self):
        select = {
            "cols": ["region", "year"],
            "aggs": [["sum", "amount"]],
            "grouping": [["region", "year"]],
        }
        out = run(ROWS, select, expand(["region", {"rollup": ["year"]}]))
        label = "grouping(region,year)"
        g = _index(out, ["region", "year"], label)
        # (region, year) detail
        self.assertEqual(g[("eu", 2025, 0)]["sum(amount)"], 30)
        self.assertEqual(g[("us", None, 0)]["sum(amount)"], 3)
        # region subtotal: year aggregated -> bit 0
        self.assertEqual(g[("eu", None, 1)]["sum(amount)"], 35)
        self.assertEqual(g[("us", None, 1)]["sum(amount)"], 10)
        # No grand-total set: plain "region" is part of every set, so no
        # row has both columns aggregated (grouping = 3 never occurs).
        # 4 (region, year) groups + 2 region subtotals
        self.assertEqual(len(out), 6)

    def test_cube_row_count(self):
        select = {"cols": ["year", "quarter"], "aggs": [["count_star", None]]}
        out = run(ROWS, select, expand([{"cube": ["year", "quarter"]}]))
        # (y,q) detail: 5 keys, (y): 3 keys (2025, 2026, NULL),
        # (q): 2 keys (1, 2) -- no NULL quarter group is invented for a
        # column outside the grouping set, (): 1
        self.assertEqual(len(out), 5 + 3 + 2 + 1)

    def test_duplicate_sets_emit_duplicate_rows_under_all(self):
        rows = [{"a": 1, "v": 2}]
        select = {"cols": ["a"], "aggs": [["count_star", None]]}
        out_all = run(rows, select, expand([{"sets": [["a"], ["a"]]}]))
        self.assertEqual(len(out_all), 2)
        out_distinct = run(
            rows, select, expand([{"sets": [["a"], ["a"]]}], distinct=True)
        )
        self.assertEqual(len(out_distinct), 1)

    def test_empty_table_rollup_only_grand_total_survives(self):
        select = {
            "cols": ["year", "quarter"],
            "aggs": [
                ["sum", "amount"],
                ["count_star", None],
                ["count", "amount"],
            ],
            "grouping": [["year", "quarter"]],
        }
        out = run([], select, expand([{"rollup": ["year", "quarter"]}]))
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["year"], None)
        self.assertEqual(row["quarter"], None)
        self.assertEqual(row["sum(amount)"], None)
        self.assertEqual(row["count_star(*)"], 0)
        self.assertEqual(row["count(amount)"], 0)
        self.assertEqual(row["grouping(year,quarter)"], 3)

    def test_empty_table_plain_group_by_yields_nothing(self):
        out = run([], {"cols": ["a"], "aggs": [["count_star", None]]}, expand(["a"]))
        self.assertEqual(out, [])

    def test_empty_table_distinct_dedups_empty_sets_before_aggregation(self):
        select = {"cols": [], "aggs": [["count_star", None]]}
        distinct = run(
            [], select, expand([{"sets": [[], []]}], distinct=True)
        )
        self.assertEqual(len(distinct), 1)
        all_sets = run([], select, expand([{"sets": [[], []]}]))
        self.assertEqual(len(all_sets), 2)

    def test_aggregates_ignore_null_values(self):
        rows = [{"a": 1, "v": None}, {"a": 1, "v": 4}]
        out = run(
            rows,
            {
                "cols": ["a"],
                "aggs": [
                    ["sum", "v"],
                    ["count", "v"],
                    ["min", "v"],
                    ["max", "v"],
                ],
            },
            expand(["a"]),
        )
        r = out[0]
        self.assertEqual(r["sum(v)"], 4)
        self.assertEqual(r["count(v)"], 1)
        self.assertEqual(r["min(v)"], 4)
        self.assertEqual(r["max(v)"], 4)

    def test_sum_over_all_nulls_is_null(self):
        rows = [{"a": 1, "v": None}]
        out = run(rows, {"cols": ["a"], "aggs": [["sum", "v"]]}, expand(["a"]))
        self.assertIsNone(out[0]["sum(v)"])


if __name__ == "__main__":
    unittest.main()
