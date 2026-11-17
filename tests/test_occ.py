import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from occ import Engine  # noqa: E402


class OccTests(unittest.TestCase):
    def test_sequential_transactions_commit(self):
        e = Engine()
        t = e.begin(); t.put("a", "1"); self.assertEqual(e.commit(t), "committed")
        t = e.begin(); self.assertEqual(t.get("a"), "1"); t.put("b", "2")
        self.assertEqual(e.commit(t), "committed")
        self.assertEqual(e.store.data, {"a": "1", "b": "2"})

    def test_lost_update_is_rejected(self):
        e = Engine()
        t0 = e.begin(); t0.put("x", "0"); e.commit(t0)
        t1, t2 = e.begin(), e.begin()
        t1.get("x"); t2.get("x")
        t1.put("x", "1"); t2.put("x", "2")
        self.assertEqual([e.commit(t1), e.commit(t2)], ["committed", "aborted"])


if __name__ == "__main__":
    unittest.main()
