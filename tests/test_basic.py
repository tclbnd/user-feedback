import unittest

from bmpkit.bmp import parse

import files


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(files.tiny())
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]), ("info", 4, 1, 24))

    def test_corner(self):
        self.assertEqual(parse(files.tiny())["corner_pixel"], "#ff0000")

    def test_dpi(self):
        self.assertEqual(parse(files.tiny())["dpi"], [72, 72])   # 2835 ppm is 72 dpi

    def test_one_bit(self):
        out = parse(files.one_bit())
        self.assertEqual((out["bit_count"], out["palette_colors"]), (1, 2))
        self.assertEqual(out["corner_pixel"], "#ffffff")

    def test_four_bit(self):
        out = parse(files.four_bit())
        self.assertEqual((out["bit_count"], out["palette_colors"]), (4, 16))
        self.assertEqual(out["corner_pixel"], "#cf0030")         # palette entry 3

    def test_default_palette_size(self):
        out = parse(files.eight_bit_default_palette())
        self.assertEqual(out["palette_colors"], 256)             # a 0 count means the full table
        self.assertEqual(out["corner_pixel"], "#050505")

    def test_top_down(self):
        out = parse(files.top_down())
        self.assertEqual((out["height"], out["top_down"]), (2, True))
        self.assertEqual(out["corner_pixel"], "#ff0000")         # the first stored row is on top
        self.assertEqual(out["image_bytes"], 24)

    def test_row_alignment(self):
        out = parse(files.aligned())
        self.assertEqual((out["row_bytes"], out["image_bytes"]), (12, 12))
        self.assertEqual(out["corner_pixel"], "#00ff00")

    def test_bitfields(self):
        out = parse(files.bitfields())
        self.assertEqual(out["compression"], "bitfields")
        self.assertEqual(out["masks"], ["0000f800", "000007e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertEqual(out["corner_pixel"], "#00ff00")         # 6-bit green scaled to 255

    def test_core(self):
        out = parse(files.core())
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]), ("core", 2, 1, 24))
        self.assertEqual(out["corner_pixel"], "#00ff00")
        self.assertEqual(out["row_bytes"], 8)                    # 6 data bytes padded to 8

    def test_core_4bit(self):
        out = parse(files.core_4bit())
        self.assertEqual(out["palette_colors"], 16)              # 3-byte palette entries
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_v4(self):
        out = parse(files.v4())
        self.assertEqual(out["header"], "v4")
        self.assertEqual(out["masks"], ["00ff0000", "0000ff00", "000000ff", "ff000000"])
        self.assertTrue(out["alpha"])
        self.assertEqual(out["color_space"], "sRGB")
        self.assertIsNone(out["profile_bytes"])
        self.assertEqual(out["dpi"], [72, 72])
        self.assertEqual(out["corner_pixel"], "#0000ff")

    def test_v5(self):
        out = parse(files.v5())
        self.assertEqual(out["header"], "v5")
        self.assertEqual(out["color_space"], "embedded")
        self.assertEqual(out["profile_bytes"], 640)
        self.assertEqual(out["corner_pixel"], "#00ff00")
