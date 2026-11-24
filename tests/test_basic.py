import unittest

from scrkit.scr import ScrError, bitmap_offset, parse, ulaplus

BLANK = bytes(6144) + bytes([0x38]) * 768


def standard(bitmap=None, attrs=None):
    bmp = bytearray(6144)
    at = bytearray([0x38]) * 768
    for offset, value in (bitmap or {}).items():
        bmp[offset] = value
    for offset, value in (attrs or {}).items():
        at[offset] = value
    return bytes(bmp + at)


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(BLANK)
        self.assertEqual((out["format"], out["width"], out["ink_pixels"]), ("standard", 256, 0))

    def test_cells(self):
        out = parse(BLANK)
        self.assertEqual((out["flashing_cells"], out["bright_cells"]), (0, 0))


class LayoutTests(unittest.TestCase):
    def test_bitmap_address_interleave(self):
        # three 2048-byte thirds, 32-byte character rows and 256-byte pixel lines
        self.assertEqual(bitmap_offset(0, 0), 0)
        self.assertEqual(bitmap_offset(0, 1), 256)
        self.assertEqual(bitmap_offset(0, 7), 7 * 256)
        self.assertEqual(bitmap_offset(0, 8), 32)
        self.assertEqual(bitmap_offset(0, 63), 7 * 32 + 7 * 256)
        self.assertEqual(bitmap_offset(0, 64), 2048)
        self.assertEqual(bitmap_offset(0, 191), 4096 + 7 * 32 + 7 * 256)
        self.assertEqual(bitmap_offset(255, 191), 4096 + 7 * 32 + 7 * 256 + 31)

    def test_lower_half_uses_its_own_bitmap(self):
        # row 96 lives at 2048 + 128, not at 96 * 32
        a96 = bitmap_offset(128, 96)
        self.assertEqual(a96, 2048 + 4 * 32 + 16)
        out = parse(standard({a96: 0x80}, {(96 >> 3) * 32 + (128 >> 3): 0x07}))
        self.assertEqual(out["points"]["center"], "#d7d7d7")
        # a stale byte at the flat y * 32 address must not be consulted
        out = parse(standard({a96: 0x80, 96 * 32: 0x00}, {(96 >> 3) * 32 + (128 >> 3): 0x07}))
        self.assertEqual(out["points"]["center"], "#d7d7d7")

    def test_pixels_are_msb_first(self):
        # ink 7 (white) on paper 0 (black): bit 7 is the leftmost pixel
        attr = {0: 0x07}
        left = parse(standard({bitmap_offset(0, 0): 0x80}, attr))
        self.assertEqual(left["points"]["top_left"], "#d7d7d7")
        # a byte ending in bit 0 paints its rightmost pixel, not its left
        left_edge = parse(standard({bitmap_offset(0, 0): 0x01}, attr))
        self.assertEqual(left_edge["points"]["top_left"], "#000000")
        rightmost = parse(standard({bitmap_offset(248, 0): 0x01}, {31: 0x07}))
        self.assertEqual(rightmost["points"]["top_right"], "#d7d7d7")
        wrong_end = parse(standard({bitmap_offset(248, 0): 0x80}, {31: 0x07}))
        self.assertEqual(wrong_end["points"]["top_right"], "#000000")

    def test_paper_ignores_bright_bit(self):
        # 0x50 is bright paper 2 (red); if bit 6 leaked into the paper number
        # it would read as paper 5 (cyan)
        self.assertEqual(parse(standard({}, {0: 0x50}))["points"]["top_left"], "#ff0000")
        # 0x40 alone is black paper, not paper 4 (green)
        self.assertEqual(parse(standard({}, {0: 0x40}))["points"]["top_left"], "#000000")

    def test_bright_lifts_every_component(self):
        normal = parse(standard({bitmap_offset(0, 0): 0x80}, {0: 0x07}))
        bright = parse(standard({bitmap_offset(0, 0): 0x80}, {0: 0x47}))
        self.assertEqual(normal["points"]["top_left"], "#d7d7d7")
        self.assertEqual(bright["points"]["top_left"], "#ffffff")
        # black stays black
        black = parse(standard({bitmap_offset(0, 0): 0x80}, {0: 0x40}))
        self.assertEqual(black["points"]["top_left"], "#000000")


class UlaplusTests(unittest.TestCase):
    def test_widening(self):
        self.assertEqual(ulaplus(0x1C), "#ff0000")
        self.assertEqual(ulaplus(0xE0), "#00ff00")
        self.assertEqual(ulaplus(0x03), "#0000ff")

    def test_palette_lookup(self):
        data = bytearray(BLANK + bytes(64))
        data[6912 + 15] = 0x1C                    # paper entry 15 -> red
        out = parse(bytes(data))
        self.assertEqual((out["format"], out["width"]), ("ulaplus", 256))
        # blank paper, attribute 0x38 selects group 0 entry 8 + 7 = 15
        self.assertEqual(out["points"]["top_left"], "#ff0000")


class TimexTests(unittest.TestCase):
    def test_hicolor_size_and_attributes(self):
        data = bytearray(6144 + 6144)
        for i in range(6144):
            data[6144 + i] = 0x38
        data[bitmap_offset(100, 70)] = 0x08       # bit 3 paints x = 100
        data[6144 + bitmap_offset(100, 70)] = 0x47  # bright white ink
        out = parse(bytes(data))
        self.assertEqual((out["format"], out["width"], out["height"]), ("timex_hicolor", 256, 192))
        self.assertEqual(out["points"]["probe"], "#ffffff")   # bright ink 7
        self.assertEqual(out["points"]["top_left"], "#d7d7d7")
        self.assertGreater(out["bright_cells"], 0)

    def test_hires_banks(self):
        data = bytearray(12289)
        data[0] = 0x80                             # bank 0, leftmost bit -> x = 0
        data[6144] = 0x80                          # bank 1, leftmost bit -> x = 8
        data[12288] = 0x02
        out = parse(bytes(data))
        self.assertEqual((out["format"], out["width"], out["height"]), ("timex_hires", 512, 192))
        self.assertEqual(out["ink_pixels"], 2)
        self.assertEqual(out["mode"], 2)
        self.assertEqual(out["points"]["top_left"], "#000000")
        self.assertEqual(out["points"]["top_right"], "#d7d7d7")
        self.assertEqual((out["flashing_cells"], out["bright_cells"]), (0, 0))

    def test_unknown_size(self):
        with self.assertRaises(ScrError):
            parse(bytes(7000))
