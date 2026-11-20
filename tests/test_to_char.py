import unittest

from numfmt.to_char import to_char


class ToCharTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(to_char(-485, "999"), "-485")
        self.assertEqual(to_char(-1485, "9,999"), "-1,485")
        self.assertIsNone(to_char(None, "999"))
        self.assertIsNone(to_char(1, None))


if __name__ == "__main__":
    unittest.main()
