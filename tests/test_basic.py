import unittest

from cpckit.cpc import parse

BLANK = bytes(16384)


class BasicTests(unittest.TestCase):
    def test_no_header(self):
        out = parse(BLANK)
        self.assertEqual((out["amsdos"], out["screen_bytes"]), (None, 16384))

    def test_blank_pens(self):
        self.assertEqual(parse(BLANK)["mode1_pens"], [64000, 0, 0, 0])
