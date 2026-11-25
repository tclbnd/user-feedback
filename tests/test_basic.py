import unittest

from samkit.screen import ScreenError, parse


class BasicTests(unittest.TestCase):
    def test_black_mode4_screen(self):
        out = parse(b"SAMSCR\x04" + bytes(16) + bytes(24576))
        self.assertEqual(out["colors"], {"#000000": 256 * 192})

    def test_not_a_dump(self):
        with self.assertRaises(ScreenError):
            parse(b"SAMXXX\x04" + bytes(40))

    def test_palette_bright_bit(self):
        # bit 6 is the shared BRIGHT bit: 0x40 is bright black, 0x7f bright white
        out = parse(b"SAMSCR\x04" + bytes([0x40, 0x7F]) + bytes(14) + bytes(24576))
        self.assertEqual(out["palette"][0], "#242424")
        self.assertEqual(out["palette"][1], "#ffffff")

    def test_mode2_screen(self):
        pal = bytes(16)
        bitmap = bytes([0xF0]) * 6144          # left half of each cell set
        attrs = bytearray(6144)
        attrs[0] = 0x01                        # ink = CLUT 8, paper = CLUT 1
        attrs[32] = 0x80 | 0x01                # same colours, flash bit set
        mem = bitmap + bytes(0x2000 - 6144) + bytes(attrs)
        out = parse(b"SAMSCR\x02" + pal + mem)
        self.assertEqual(out["mode"], 2)
        self.assertEqual(out["flash_cells"], 1)
        self.assertEqual(out["colors"], {"#000000": 256 * 192})
        self.assertEqual(out["first_line"], [["#000000", 256]])

    def test_mode2_truncated(self):
        with self.assertRaises(ScreenError):
            parse(b"SAMSCR\x02" + bytes(16) + bytes(100))
