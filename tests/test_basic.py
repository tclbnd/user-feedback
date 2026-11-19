import unittest

from strfn.registry import FUNCTIONS as F


class BasicTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(F["upper"]("abc"), "ABC")
        self.assertEqual(F["lpad"]("7", 3, "0"), "007")
        self.assertEqual(F["split_part"]("a,b,c", ",", 2), "b")
        self.assertIsNone(F["||"]("a", None))
        self.assertEqual(F["format"]("%s-%s", "a", "b"), "a-b")


if __name__ == "__main__":
    unittest.main()
