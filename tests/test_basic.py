import unittest

from jcall import call


class BasicTests(unittest.TestCase):
    def test_array_concat(self):
        self.assertEqual(call(["||", "[1, 2]", "[3]"])["value"], "[1, 2, 3]")

    def test_set_existing(self):
        self.assertEqual(call(["jsonb_set", '{"a": 1}', ["a"], "2"])["value"], '{"a": 2}')


if __name__ == "__main__":
    unittest.main()
