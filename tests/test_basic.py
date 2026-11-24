import unittest

from hgrkit.hgr import parse

BLANK = bytes(8192)


class BasicTests(unittest.TestCase):
    def test_blank(self):
        out = parse(BLANK)
        self.assertEqual((out["lit_pixels"], out["group1_bytes"]), (0, 0))

    def test_black(self):
        self.assertEqual(parse(BLANK)["points"]["center"], "black")
