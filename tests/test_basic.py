import unittest

from pixkit.pix import PixError, parse


class BasicTests(unittest.TestCase):
    def test_bad_magic(self):
        with self.assertRaises(PixError):
            parse(bytes(32))

    def test_format_list(self):
        from pixkit.pix import FORMATS
        self.assertIn(3, FORMATS)


if __name__ == "__main__":
    unittest.main()
