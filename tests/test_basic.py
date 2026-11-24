import unittest

from scrkit.scr import parse

BLANK = bytes(6144) + bytes([0x38]) * 768


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(BLANK)
        self.assertEqual((out["format"], out["width"], out["ink_pixels"]), ("standard", 256, 0))

    def test_cells(self):
        out = parse(BLANK)
        self.assertEqual((out["flashing_cells"], out["bright_cells"]), (0, 0))
