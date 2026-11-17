import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bptree import run  # noqa: E402


def leaves(tree):
    nodes, out, level = tree["nodes"], [], [tree["root"]]
    while level:
        if nodes[str(level[0])]["leaf"]:
            return [nodes[str(n)]["keys"] for n in level]
        level = [c for n in level for c in nodes[str(n)]["children"]]


class TreeTests(unittest.TestCase):
    def test_single_leaf(self):
        out = run({"initial": [3, 1, 2], "threads": [[{"op": "scan", "lo": 0, "hi": 9}]]}, 0)
        self.assertEqual(out["results"][0][0]["keys"], [1, 2, 3])

    def test_leaf_split(self):
        out = run({"initial": [1, 2, 3, 4, 5], "threads": []}, 0)
        self.assertEqual(leaves(out["tree"]), [[1, 2, 3], [4, 5]])

    def test_single_thread_scan(self):
        out = run({"initial": list(range(20)), "threads": [[{"op": "scan", "lo": 5, "hi": 12}]]}, 3)
        self.assertEqual(out["results"][0][0]["keys"], list(range(5, 13)))


if __name__ == "__main__":
    unittest.main()
