import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import execute  # noqa: E402
from planner import choose  # noqa: E402

DB = {"rows": [{"id": i, "a": i % 3, "b": i} for i in range(1, 10)],
      "indexes": [{"name": "ix_a", "columns": ["a"]}]}


class IdxqTests(unittest.TestCase):
    def test_equality_on_first_column_uses_index(self):
        plan = choose(DB, {"where": [["a", "=", 1]], "select": ["id"]})
        self.assertEqual((plan["index"], plan["eq"]), ("ix_a", ["a"]))

    def test_rows_match_condition(self):
        q = {"where": [["a", "=", 2]], "select": ["id", "b"]}
        rows, _, _ = execute(DB, q, choose(DB, q))
        self.assertEqual(sorted(r["id"] for r in rows), [2, 5, 8])


if __name__ == "__main__":
    unittest.main()
