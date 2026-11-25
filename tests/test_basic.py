import unittest

from tiakit.playfield import TiaError, parse


class BasicTests(unittest.TestCase):
    def test_solid_wall(self):
        rec = bytes([0x00, 0x0E, 0, 0, 0, 0xF0, 0xFF, 0xFF])
        out = parse(b"TIALOG" + bytes([1, 0]) + rec)
        self.assertEqual(out["first_line"], [["0e", 160]])

    def test_not_a_log(self):
        with self.assertRaises(TiaError):
            parse(b"TIAXXX\x00\x00")
