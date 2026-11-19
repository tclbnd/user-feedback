import unittest

from bitmap.heap import Heap
from bitmap.vacuum import stats


class HeapTests(unittest.TestCase):
    def test_update_chain(self):
        heap = Heap()
        tid = heap.insert({"id": 1, "amount": 5})
        new_tid, changed = heap.update(tid, {"amount": 7})
        self.assertEqual(changed, ["amount"])
        self.assertEqual(heap.fetch_live(tid), (new_tid, heap.fetch(new_tid)))
        self.assertEqual(heap.fetch_live(tid)[1]["row"]["amount"], 7)
        self.assertEqual(stats(heap)["chains"], 1)


if __name__ == "__main__":
    unittest.main()
