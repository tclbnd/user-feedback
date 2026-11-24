import unittest

from pcxkit.pcx import parse

from images import tiny


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(tiny())
        self.assertEqual((out["version"], out["rle"], out["height"], out["dpi"]), (5, False, 1, [72, 72]))

    def test_lines(self):
        out = parse(tiny())
        self.assertEqual((out["decoded_lines"], out["palette"]), (1, "none"))
