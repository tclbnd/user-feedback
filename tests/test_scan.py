import unittest

from bitmap.btree import Index
from bitmap.executor import bitmap_scan, seq_scan
from bitmap.heap import Heap


def table(n=40):
    heap = Heap()
    for i in range(1, n + 1):
        heap.insert({"id": i, "region": ["eu", "us"][i % 2], "amount": i * 10})
    return heap, {c: Index(heap, c) for c in ("region", "amount")}


class ScanTests(unittest.TestCase):
    def test_single_predicate(self):
        heap, ix = table()
        cond = {"col": "region", "op": "=", "value": "eu"}
        self.assertEqual(bitmap_scan(heap, ix, cond)["ids"], seq_scan(heap, cond))

    def test_and_small(self):
        heap, ix = table()
        cond = {"and": [{"col": "region", "op": "=", "value": "us"}, {"col": "amount", "op": "<", "value": 150}]}
        self.assertEqual(bitmap_scan(heap, ix, cond)["ids"], [1, 3, 5, 7, 9, 11, 13])

    def test_or_small(self):
        heap, ix = table()
        cond = {"or": [{"col": "amount", "op": "<=", "value": 20}, {"col": "amount", "op": ">=", "value": 390}]}
        self.assertEqual(bitmap_scan(heap, ix, cond)["ids"], [1, 2, 39, 40])


if __name__ == "__main__":
    unittest.main()
