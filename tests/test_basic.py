import unittest

from castcall import call


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(call(["42", "text", "int4"])["value"], "42")
        self.assertEqual(call(["true", "text", "bool"])["value"], "t")
        self.assertEqual(call(["123.456", "numeric", "numeric(5,2)"])["value"], "123.46")


if __name__ == "__main__":
    unittest.main()
