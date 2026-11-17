import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lockmgr import LockTable  # noqa: E402
from locksim import run  # noqa: E402


class LockTests(unittest.TestCase):
    def test_shared_locks_coexist(self):
        t = LockTable()
        self.assertTrue(t.request(1, "r", "S"))
        self.assertTrue(t.request(2, "r", "S"))
        self.assertFalse(t.request(3, "r", "X"))

    def test_waiter_runs_after_commit(self):
        ops = [{"tx": 1, "op": "begin"}, {"tx": 2, "op": "begin"},
               {"tx": 1, "op": "write", "res": "a"}, {"tx": 2, "op": "write", "res": "a"},
               {"tx": 1, "op": "commit"}, {"tx": 2, "op": "commit"}]
        out = run({"ops": ops})
        self.assertEqual(out["committed"], [1, 2])


if __name__ == "__main__":
    unittest.main()
