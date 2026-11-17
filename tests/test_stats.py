import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selectivity import estimate  # noqa: E402
from stats import analyze  # noqa: E402
from statsdb import Table  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
