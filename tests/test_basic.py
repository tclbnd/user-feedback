import unittest

from scrkit.scr import bitmap_offset, parse

BLANK = bytes(6144) + bytes([0x38]) * 768


class BasicTests(unittest.TestCase):
    def test_format(self):
        out = parse(BLANK)
        self.assertEqual((out["format"], out["width"], out["ink_pixels"]), ("standard", 256, 0))

    def test_cells(self):
        out = parse(BLANK)
        self.assertEqual((out["flashing_cells"], out["bright_cells"]), (0, 0))


class LayoutTests(unittest.TestCase):
    def test_interleaved_rows(self):
        # thirds of 2048; within a third, pixel rows of a character row are 256 apart
        self.assertEqual(bitmap_offset(0, 0), 0)
        self.assertEqual(bitmap_offset(0, 1), 256)
        self.assertEqual(bitmap_offset(0, 8), 32)
        self.assertEqual(bitmap_offset(5, 9), 256 + 32 + 5)
        self.assertEqual(bitmap_offset(0, 64), 2048)
        self.assertEqual(bitmap_offset(0, 128), 4096)
        self.assertEqual(bitmap_offset(31, 191), 6143)

    def test_pixel_bit_order_and_bright(self):
        d = bytearray(6912)
        d[bitmap_offset(0, 0)] = 0x80          # bit 7 is the leftmost pixel
        d[6144] = 0b01_010_111                 # bright, paper 2 (red), ink 7 (white)
        out = parse(bytes(d))
        self.assertEqual(out["points"]["top_left"], "#ffffff")
        d[6144] = 0b00_010_111
        self.assertEqual(parse(bytes(d))["points"]["top_left"], "#d7d7d7")

    def test_paper_bits(self):
        d = bytearray(6912)
        d[6144] = 0b00_010_111                 # pixel clear: paper 2 (red)
        self.assertEqual(parse(bytes(d))["points"]["top_left"], "#d70000")


class UlaPlusTests(unittest.TestCase):
    def make(self, attr, pal_entries, bit=0x80):
        d = bytearray(6976)
        d[bitmap_offset(0, 0)] = bit
        d[6144] = attr
        for i, v in pal_entries.items():
            d[6912 + i] = v
        return parse(bytes(d))

    def test_ink(self):
        out = self.make((0 << 6) | 3, {0 * 16 + 3: 0b111_000_00})
        self.assertEqual((out["format"], out["width"]), ("ulaplus", 256))
        self.assertEqual(out["points"]["top_left"], "#00ff00")

    def test_paper_group(self):
        out = self.make((2 << 6) | (1 << 3), {2 * 16 + 8 + 1: 0b000_000_11}, bit=0x00)
        self.assertEqual(out["points"]["top_left"], "#0000ff")

    def test_widening(self):
        out = self.make(0, {0: 0b101_101_10})            # ink 0 of group 0: r=5, g=5, b=2
        self.assertEqual(out["points"]["top_left"], "#b6b6aa")


class TimexHicolorTests(unittest.TestCase):
    def test_attr_per_raster_cell(self):
        d = bytearray(12288)
        d[bitmap_offset(0, 5)] = 0x80
        d[6144 + bitmap_offset(0, 5)] = 0b00_001_010   # paper 1, ink 2
        out = parse(bytes(d))
        self.assertEqual((out["format"], out["width"], out["ink_pixels"]), ("timex_hicolor", 256, 1))


class TimexHiresTests(unittest.TestCase):
    def test_banks_and_mode_colours(self):
        d = bytearray(12289)
        d[12288] = (1 << 3) | 6                        # combo 1: blue ink on yellow paper
        d[bitmap_offset(0, 0)] = 0x80                  # bank 1, byte col 0 -> x=0
        d[6144 + bitmap_offset(0, 0)] = 0x40           # bank 2, byte col 0 -> x=14
        out = parse(bytes(d))
        self.assertEqual((out["format"], out["width"]), ("timex_hires", 512))
        self.assertEqual(out["mode"], (1 << 3) | 6)
        self.assertEqual(out["points"]["top_left"], "#0000d7")
        self.assertEqual(out["points"]["top_right"], "#d7d700")
        self.assertEqual(out["ink_pixels"], 2)
        self.assertEqual((out["flashing_cells"], out["bright_cells"]), (0, 0))

    def test_default_combo_black_on_white(self):
        d = bytearray(12289)
        d[6144 + bitmap_offset(12, 70)] = 0x80           # probe x=200: col 25 (odd), bank 2 byte 12, bit 7
        out = parse(bytes(d))
        self.assertEqual(out["points"]["probe"], "#000000")
        self.assertEqual(out["points"]["top_left"], "#d7d7d7")


if __name__ == "__main__":
    unittest.main()
