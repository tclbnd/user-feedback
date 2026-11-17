import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heap import Heap  # noqa: E402


class HeapTests(unittest.TestCase):
    def test_insert_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rids = [h.insert(f"row{i}") for i in range(3)]
            self.assertEqual([h.read(r) for r in rids], ["row0", "row1", "row2"])

    def test_shrinking_update_stays_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            h = Heap(d)
            rid = h.insert("a" * 40)
            h.update(rid, "short")
            self.assertEqual(h.read(rid), "short")


if __name__ == "__main__":
    unittest.main()
