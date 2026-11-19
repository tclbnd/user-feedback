import unittest

from rangecall import call


def value(c):
    return call(c)["value"]


class RangeTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(value(["int4range", "[1,5)"]), "[1,5)")
        self.assertEqual(value(["lower", ["int4range", "[1,5)"]]), "1")
        self.assertEqual(value(["&&", ["int4range", "[1,5)"], ["int4range", "[3,8)"]]), "t")
        self.assertEqual(value(["isempty", ["numrange", "empty"]]), "t")
        self.assertEqual(value(["upper_inf", ["int4range", "[1,)"]]), "t")


if __name__ == "__main__":
    unittest.main()
