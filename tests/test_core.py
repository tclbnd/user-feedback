import unittest

from numfn.registry import FUNCTIONS as F


class CoreTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(F["+"](2, 3), "5")
        self.assertEqual(F["abs"](-4), "4")
        self.assertEqual(F["floor"]("3.7"), "3")
        self.assertEqual(F["gcd"](12, 18), "6")
        self.assertIsNone(F["round"](None))


if __name__ == "__main__":
    unittest.main()
