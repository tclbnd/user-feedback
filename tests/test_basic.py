import unittest

from numcall import call


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(call(["sign", ["numeric", "-3"]])["value"], "-1")
        self.assertEqual(call(["gcd", ["int", 12], ["int", 18]])["value"], "6")


if __name__ == "__main__":
    unittest.main()
