import unittest

from bmpkit.bmp import parse

from files import tiny


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(tiny())
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]), ("info", 4, 1, 24))

    def test_corner(self):
        self.assertEqual(parse(tiny())["corner_pixel"], "#ff0000")
