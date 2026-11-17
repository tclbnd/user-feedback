import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregate import run  # noqa: E402


class AggregateTests(unittest.TestCase):
    def test_sum_per_group(self):
        q = {"rows": [{"g": "a", "x": 1}, {"g": "b", "x": 2}, {"g": "a", "x": 3}],
             "group_by": ["g"], "aggregates": [{"as": "s", "fn": "sum", "arg": "x"}]}
        self.assertEqual([(r["g"], r["s"]) for r in run(q)["rows"]], [("a", 4), ("b", 2)])

    def test_count_star(self):
        q = {"rows": [{"g": 1}, {"g": 1}], "group_by": ["g"], "aggregates": [{"as": "n", "fn": "count", "arg": "*"}]}
        self.assertEqual(run(q)["rows"][0]["n"], 2)


if __name__ == "__main__":
    unittest.main()
