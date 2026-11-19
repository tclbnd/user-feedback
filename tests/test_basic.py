import unittest

from gsets.aggregate import run
from gsets.clauses import expand

ROWS = [{"a": 1, "v": 2}, {"a": 1, "v": 3}, {"a": 2, "v": 5}]


class BasicTests(unittest.TestCase):
    def test_plain_group_by(self):
        out = run(ROWS, {"cols": ["a"], "aggs": [["sum", "v"]]}, expand(["a"]))
        self.assertEqual(sorted((r["a"], r["sum(v)"]) for r in out), [(1, 5), (2, 5)])

    def test_rollup_one_column(self):
        self.assertEqual(expand([{"rollup": ["a"]}]), [["a"], []])


if __name__ == "__main__":
    unittest.main()
