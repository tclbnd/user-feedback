import unittest

from arrcall import call


class BasicTests(unittest.TestCase):
    def test_append_and_position(self):
        self.assertEqual(call(["array_append", [1, 2], 3])["value"], [1, 2, 3])
        self.assertEqual(call(["array_position", ["a", "b"], "b"])["value"], 2)


if __name__ == "__main__":
    unittest.main()
