import unittest

from rcte.bom import explode
from rcte.graph import Graph
from rcte.recursive import evaluate

TREE = Graph([{"id": x} for x in "abcd"], [{"src": "a", "dst": "b"}, {"src": "a", "dst": "c"}, {"src": "c", "dst": "d"}])


class RecursiveTests(unittest.TestCase):
    def test_tree_depths(self):
        rows = evaluate(TREE, {"anchor": {"id": "a"}, "union": "all", "columns": ["id", "depth"]})
        self.assertEqual(sorted((r["id"], r["depth"]) for r in rows), [("a", 0), ("b", 1), ("c", 1), ("d", 2)])

    def test_paths(self):
        rows = evaluate(TREE, {"anchor": {"id": "a"}, "union": "all", "columns": ["id", "path"]})
        self.assertIn({"id": "d", "path": ["a", "c", "d"]}, rows)

    def test_explode_multiplies_quantities(self):
        need = explode(TREE, "a", {("a", "c"): 2, ("c", "d"): 3})
        self.assertEqual(need, {"b": 1, "c": 2, "d": 6})


if __name__ == "__main__":
    unittest.main()
