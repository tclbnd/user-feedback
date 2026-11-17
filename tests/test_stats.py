import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selectivity import estimate  # noqa: E402
from stats import analyze  # noqa: E402
from statsdb import Table, matches  # noqa: E402


def true_frac(columns, expr):
    names = list(columns)
    rows = [dict(zip(names, vals)) for vals in zip(*columns.values())]
    return sum(matches(r, expr) for r in rows) / len(rows)


class StatsTests(unittest.TestCase):
    def test_null_fraction(self):
        st = analyze({"columns": {"a": [1, None, 2, None]}})["columns"]["a"]
        self.assertEqual(st["null_frac"], 0.5)
        self.assertEqual(estimate({"rows": 4, "columns": {"a": st}}, {"col": "a", "op": "is_null"}), 0.5)

    def test_first_estimate_analyzes(self):
        t = Table({"columns": {"a": list(range(100))}, "indexes": ["a"]})
        res = t.apply({"op": "estimate", "where": {"col": "a", "op": "=", "value": 3}})
        self.assertTrue(res["analyzed"])
        self.assertEqual(res["path"], "index")


class AcceptanceTests(unittest.TestCase):
    """Every predicate type on skewed, uniform, null-heavy and single-value
    columns must land within 0.02 + 20% of the true matching fraction."""

    def check(self, columns, exprs):
        stats = analyze({"columns": columns})
        for expr in exprs:
            with self.subTest(where=expr):
                got = estimate(stats, expr)
                want = true_frac(columns, expr)
                self.assertAlmostEqual(got, want, delta=0.02 + 0.2 * want)

    def exprs_for(self, col, hot, cold, lo, hi):
        # AND/OR combine the tested column with an independent second column
        # "b" (the spec folds AND as a product and OR as s1+s2-s1*s2, which
        # only tracks the true fraction for independent predicates).
        return [
            {"col": col, "op": "=", "value": hot},
            {"col": col, "op": "=", "value": cold},
            {"col": col, "op": "!=", "value": hot},
            {"col": col, "op": "<", "value": lo},
            {"col": col, "op": "<=", "value": lo},
            {"col": col, "op": ">", "value": hi},
            {"col": col, "op": ">=", "value": hi},
            {"col": col, "op": "in", "values": [hot, cold, hot]},
            {"col": col, "op": "is_null"},
            {"col": col, "op": "not_null"},
            {"and": [{"col": col, "op": ">=", "value": lo},
                     {"col": "b", "op": "<", "value": 30}]},
            {"or": [{"col": col, "op": "=", "value": hot},
                    {"col": "b", "op": ">=", "value": 90}]},
        ]

    def with_b(self, col, seed=0):
        rng = random.Random(seed)
        rng.shuffle(col)
        return {"x": col, "b": [i % 100 for i in range(len(col))]}

    def test_skewed_column(self):
        rng = random.Random(7)
        col = ([7] * 1000 + [13] * 400 + [21] * 100
               + [rng.randrange(100, 1100) for _ in range(480)] + [None] * 20)
        cols = self.with_b(col, seed=1)
        self.check(cols, self.exprs_for("x", 7, 640, 300, 900))

    def test_uniform_column(self):
        col = [i % 200 for i in range(2000)]
        cols = self.with_b(col, seed=2)
        self.check(cols, self.exprs_for("x", 42, 187, 50, 150))

    def test_null_heavy_column(self):
        rng = random.Random(3)
        col = [None] * 1400 + [rng.randrange(1, 51) for _ in range(600)]
        cols = self.with_b(col, seed=3)
        self.check(cols, self.exprs_for("x", 25, 3, 10, 40))

    def test_single_value_column(self):
        col = [5] * 1900 + [None] * 100
        exprs = [
            {"col": "s", "op": "=", "value": 5},
            {"col": "s", "op": "=", "value": 6},
            {"col": "s", "op": "!=", "value": 5},
            {"col": "s", "op": "<", "value": 5},
            {"col": "s", "op": "<=", "value": 5},
            {"col": "s", "op": ">", "value": 5},
            {"col": "s", "op": ">=", "value": 5},
            {"col": "s", "op": "in", "values": [5, 6]},
            {"col": "s", "op": "is_null"},
            {"col": "s", "op": "not_null"},
        ]
        self.check({"s": col}, exprs)

    def test_hot_and_cold_pick_different_paths(self):
        rng = random.Random(11)
        col = [7] * 1000 + [rng.randrange(100, 1100) for _ in range(1000)]
        rng.shuffle(col)
        t = Table({"columns": {"k": col}, "indexes": ["k"]})
        hot = t.apply({"op": "estimate", "where": {"col": "k", "op": "=", "value": 7}})
        cold = t.apply({"op": "estimate", "where": {"col": "k", "op": "=", "value": 640}})
        self.assertEqual(hot["path"], "seq")
        self.assertEqual(cold["path"], "index")
        self.assertGreater(hot["selectivity"], 100 * cold["selectivity"])

    def test_deletes_count_toward_staleness(self):
        t = Table({"columns": {"k": list(range(1, 201))}, "indexes": []})
        t.apply({"op": "analyze"})
        t.apply({"op": "delete", "where": {"col": "k", "op": "<=", "value": 71}})
        res = t.apply({"op": "estimate", "where": {"col": "k", "op": "=", "value": 150}})
        self.assertTrue(res["analyzed"])  # 71 deletions > 50 + 0.1 * 200

    def test_small_changes_keep_stats(self):
        t = Table({"columns": {"k": list(range(1, 201))}, "indexes": []})
        t.apply({"op": "analyze"})
        t.apply({"op": "insert", "columns": {"k": [500] * 10}})
        res = t.apply({"op": "estimate", "where": {"col": "k", "op": "=", "value": 150}})
        self.assertFalse(res["analyzed"])  # 10 modifications < 50 + 0.1 * 200

    def test_sampling_large_table(self):
        from stats import sample
        vals = [i % 500 for i in range(9000)]
        self.assertEqual(sample(vals), vals[::3])  # every 3rd row from the first

    def test_out_of_range_values_use_rest_share(self):
        stats = analyze({"columns": {"a": [1] * 8 + list(range(10, 60))}})
        st = stats["columns"]["a"]
        rest = 1.0 - st["null_frac"] - sum(f for _, f in st["mcv"])
        per_value = rest / (st["ndv"] - len(st["mcv"]))
        est = lambda expr: estimate(stats, expr)
        # constants outside the histogram range fall back to the leftover share
        self.assertAlmostEqual(est({"col": "a", "op": "=", "value": 5}), per_value)
        self.assertAlmostEqual(est({"col": "a", "op": "=", "value": 9999}), per_value)
        # ranges outside the histogram bounds clip to 0 / the MCV share
        self.assertEqual(est({"col": "a", "op": "<", "value": 0}), 0.0)
        self.assertAlmostEqual(est({"col": "a", "op": ">", "value": 1000}), 0.0)
        self.assertAlmostEqual(est({"col": "a", "op": "!=", "value": 1}), 1.0 - 8 / 58)


if __name__ == "__main__":
    unittest.main()
