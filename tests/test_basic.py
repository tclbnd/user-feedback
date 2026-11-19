import unittest

from patcall import call


class BasicTests(unittest.TestCase):
    def test_like_basics(self):
        self.assertTrue(call(["like", "abc", "a%"])["value"])
        self.assertFalse(call(["like", "abc", "b%"])["value"])
        self.assertTrue(call(["like", "abc", "a_c"])["value"])


if __name__ == "__main__":
    unittest.main()
