import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lockmgr import COMPAT, LockTable, join  # noqa: E402
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


class CompatibilityMatrixTests(unittest.TestCase):
    """The matrix from the README, checked symmetrically."""

    MATRIX = {
        "IS":  {"IS": True,  "IX": True,  "S": True,  "SIX": True,  "X": False},
        "IX":  {"IS": True,  "IX": True,  "S": False, "SIX": False, "X": False},
        "S":   {"IS": True,  "IX": False, "S": True,  "SIX": False, "X": False},
        "SIX": {"IS": True,  "IX": False, "S": False, "SIX": False, "X": False},
        "X":   {"IS": False, "IX": False, "S": False, "SIX": False, "X": False},
    }

    def test_matrix(self):
        for held, row in self.MATRIX.items():
            for wanted, ok in row.items():
                self.assertEqual(wanted in COMPAT[held], ok, (held, wanted))
                self.assertEqual(held in COMPAT[wanted], ok, (wanted, held))

    def test_intention_locks_coexist(self):
        t = LockTable()
        self.assertTrue(t.request(1, "r", "IS"))
        self.assertTrue(t.request(2, "r", "IX"))
        self.assertTrue(t.request(3, "r", "IS"))

    def test_ix_blocks_s(self):
        t = LockTable()
        self.assertTrue(t.request(1, "r", "IX"))
        self.assertFalse(t.request(2, "r", "S"))


class ConversionTests(unittest.TestCase):
    def test_join_modes(self):
        self.assertEqual(join("S", "S"), "S")
        self.assertEqual(join("S", "IS"), "S")
        self.assertEqual(join("IS", "S"), "S")
        self.assertEqual(join("S", "IX"), "SIX")
        self.assertEqual(join("IX", "S"), "SIX")
        self.assertEqual(join("IS", "X"), "X")
        self.assertEqual(join("SIX", "X"), "X")
        self.assertEqual(join("S", "X"), "X")

    def test_covered_conversion_grants_at_once(self):
        t = LockTable()
        self.assertEqual(t.request(1, "r", "SIX"), "SIX")
        self.assertEqual(t.request(1, "r", "IS"), "SIX")
        self.assertEqual(t.request(1, "r", "S"), "SIX")

    def test_six_upgrade(self):
        t = LockTable()
        self.assertEqual(t.request(1, "r", "S"), "S")
        self.assertEqual(t.request(1, "r", "IX"), "SIX")
        self.assertEqual(t.resources["r"].granted[1], "SIX")


class QueueFairnessTests(unittest.TestCase):
    def test_exclusive_request_is_not_starved(self):
        t = LockTable()
        t.request(1, "r", "S")
        # An X request queues; later S requests must queue behind it.
        self.assertFalse(t.request(2, "r", "X"))
        self.assertFalse(t.request(3, "r", "S"))
        t.release_all(1)
        # Only the head X can be granted; the trailing S stays queued.
        self.assertEqual(t.resources["r"].granted.get(2), "X")
        self.assertNotIn(3, t.resources["r"].granted)
        self.assertTrue(t.is_waiting(3))

    def test_grant_advances_until_first_incompatible(self):
        t = LockTable()
        t.request(1, "r", "X")
        t.request(2, "r", "S")
        t.request(3, "r", "S")
        t.request(4, "r", "X")
        woken, _ = t.release_all(1)
        self.assertEqual([w[0] for w in woken], [2, 3])
        self.assertTrue(t.is_waiting(4))

    def test_conversion_waits_at_queue_head(self):
        t = LockTable()
        t.request(1, "r", "S")
        t.request(2, "r", "S")
        t.request(3, "r", "X")           # ordinary request, tail
        self.assertFalse(t.request(1, "r", "X"))  # conversion, head
        self.assertEqual([q[0] for q in t.resources["r"].queue], [1, 3])
        # The conversion only contends with the other holder.
        woken, _ = t.release_all(2)
        self.assertEqual([w[0] for w in woken], [1])


if __name__ == "__main__":
    unittest.main()
