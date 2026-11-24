import unittest

from sc2kit.sc2 import parse

BLANK = bytes([0xFE, 0, 0, 0xFF, 0x37, 0, 0]) + bytes(0x3800)


class BasicTests(unittest.TestCase):
    def test_blank_colour(self):
        self.assertEqual(parse(BLANK)["points"]["top_left"], "transparent")

    def test_not_bsave(self):
        with self.assertRaises(ValueError):
            parse(b"\x00" * 10)
