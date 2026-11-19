import unittest

from orderidx.btree import BTree
from orderidx.planner import execute

ROWS = [{"id": i, "a": v} for i, v in enumerate([5, 3, 9, 1], 1)]


class BasicTests(unittest.TestCase):
    def test_index_ascending(self):
        ix = BTree("ix", [{"col": "a"}]).build(ROWS)
        out = execute({"order_by": [{"col": "a"}], "limit": 2}, ROWS, [ix])
        self.assertEqual(out, {"path": "index scan ix", "ids": [4, 2]})

    def test_sort_without_index(self):
        out = execute({"order_by": [{"col": "a", "dir": "desc"}]}, ROWS, [])
        self.assertEqual(out["ids"], [3, 1, 2, 4])


if __name__ == "__main__":
    unittest.main()
