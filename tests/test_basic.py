import struct
import unittest

from bmpkit.bmp import parse

try:
    from tests import files
except ImportError:  # plain discovery with tests/ on the path
    import files


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(files.tiny())
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]),
                         ("info", 4, 1, 24))

    def test_corner(self):
        self.assertEqual(parse(files.tiny())["corner_pixel"], "#ff0000")


class PaletteTests(unittest.TestCase):
    def test_1bpp_index(self):
        # first byte 0x80 -> index 1 (MSB first); palette[1] = (b=1,g=2,r=3)
        bmp = files.paletted(1, width=8, height=1)
        out = parse(bmp)
        self.assertEqual(out["palette_colors"], 2)
        self.assertEqual(out["corner_pixel"], "#030201")

    def test_4bpp_index(self):
        # first byte 0x40 -> index 4 (high nibble); palette[4] = (b=4,g=8,r=12)
        bmp = files.paletted(4, width=8, height=1)
        out = parse(bmp)
        self.assertEqual(out["palette_colors"], 16)
        self.assertEqual(out["corner_pixel"], "#0c0804")

    def test_8bpp_default_palette(self):
        # clrUsed == 0 means all 256 colour table entries are present
        bmp = files.paletted(8, width=4, height=1, rows=[bytes([1, 0, 0, 0])])
        out = parse(bmp)
        self.assertEqual(out["palette_colors"], 256)
        self.assertEqual(out["corner_pixel"], "#030201")

    def test_clrused_explicit(self):
        bmp = files.paletted(8, width=2, height=1, used=4,
                             palette=bytes([10, 20, 30, 0]) * 4,
                             rows=[bytes([1, 0]) + bytes(2)])
        out = parse(bmp)
        self.assertEqual(out["palette_colors"], 4)
        self.assertEqual(out["corner_pixel"], "#1e140a")


class RowTests(unittest.TestCase):
    def test_stride_pads_to_four_bytes(self):
        # 37 px * 8 bpp = 37 bytes -> padded to 40
        out = parse(files.paletted(8, width=37, height=2))
        self.assertEqual(out["row_bytes"], 40)
        self.assertEqual(out["image_bytes"], 80)
        # 17 px * 1 bpp = 3 bytes -> padded to 4
        out1 = parse(files.paletted(1, width=17, height=2))
        self.assertEqual(out1["row_bytes"], 4)
        self.assertEqual(out1["image_bytes"], 8)

    def test_top_down_negative_height(self):
        bmp = files.paletted(8, top_down=True, width=4, height=2,
                             rows=[bytes([1, 0, 0, 0]), bytes([2, 0, 0, 0])])
        out = parse(bmp)
        self.assertTrue(out["top_down"])
        self.assertEqual(out["height"], 2)
        self.assertEqual(out["image_bytes"], 8)
        # top row is the first stored row -> index 1
        self.assertEqual(out["corner_pixel"], "#030201")

    def test_bottom_up_uses_last_stored_row(self):
        bmp = files.paletted(8, width=4, height=2,
                             rows=[bytes([2, 0, 0, 0]), bytes([1, 0, 0, 0])])
        out = parse(bmp)
        self.assertFalse(out["top_down"])
        self.assertEqual(out["corner_pixel"], "#030201")


class DpiTests(unittest.TestCase):
    def test_ppm_to_dpi(self):
        # 2835 ppm ~= 72 dpi; 3780 ppm ~= 96 dpi
        bmp = files.info_header(1, 1, 24, xppm=3780, yppm=2835)
        out = parse(files._file(bmp, bytes(4)))
        self.assertEqual(out["dpi"], [96, 72])


class MaskTests(unittest.TestCase):
    def test_rgb16_default_masks_555(self):
        dib = files.info_header(2, 1, 16)
        # 5-5-5: value 0xFFFF -> r/g/b all 31 -> white
        out = parse(files._file(dib, struct.pack("<HH", 0xFFFF, 0)))
        self.assertEqual(out["masks"], ["00007c00", "000003e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertEqual(out["corner_pixel"], "#ffffff")

    def test_rgb32_default_masks(self):
        dib = files.info_header(1, 1, 32)
        out = parse(files._file(dib, bytes([0x10, 0x20, 0x30, 0xFF])))
        self.assertEqual(out["masks"], ["00ff0000", "0000ff00", "000000ff", "00000000"])
        self.assertEqual(out["corner_pixel"], "#302010")

    def test_bitfields_40_three_masks_no_alpha(self):
        bmp = files.bitfields_40(height=1, rows=[struct.pack("<H", 0xF800) + bytes(6)])
        out = parse(bmp)
        self.assertEqual(out["compression"], "bitfields")
        self.assertEqual(out["masks"], ["0000f800", "000007e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_alphabitfields_40_four_masks(self):
        bmp = files.bitfields_40(bpp=32, alpha_mask=0xFF000000,
                                 rows=[bytes([0x00, 0x00, 0xFF, 0x80])])
        out = parse(bmp)
        self.assertEqual(out["compression"], "alphabitfields")
        self.assertEqual(out["masks"][3], "ff000000")
        self.assertTrue(out["alpha"])

    def test_v2_three_masks(self):
        bmp = files.v_header(52)
        out = parse(bmp)
        self.assertEqual(out["header"], "v2")
        self.assertEqual(out["masks"], ["00ff0000", "0000ff00", "000000ff", "00000000"])
        self.assertIsNone(out["color_space"])

    def test_v3_alpha_mask(self):
        bmp = files.v_header(56, masks=(0xFF0000, 0x00FF00, 0x0000FF, 0xFF000000))
        out = parse(bmp)
        self.assertEqual(out["header"], "v3")
        self.assertEqual(out["masks"][3], "ff000000")
        self.assertTrue(out["alpha"])


class ColorSpaceTests(unittest.TestCase):
    def test_v4_known_spaces(self):
        for code, name in ((0x73524742, "sRGB"), (0x57696E20, "windows"),
                           (0x4C494E4B, "linked"), (0x4D424544, "embedded"),
                           (0, "calibrated")):
            out = parse(files.v_header(108, cs=code))
            self.assertEqual(out["header"], "v4")
            self.assertEqual(out["color_space"], name)
            self.assertIsNone(out["profile_bytes"])

    def test_v4_unknown_space(self):
        out = parse(files.v_header(108, cs=0x12345678))
        self.assertEqual(out["color_space"], "type_12345678")

    def test_v5_embedded_profile_size(self):
        bmp = files.v_header(124, cs=0x4D424544, profile_size=1234)
        out = parse(bmp)
        self.assertEqual(out["header"], "v5")
        self.assertEqual(out["color_space"], "embedded")
        self.assertEqual(out["profile_bytes"], 1234)

    def test_v5_srgb_has_no_profile_size(self):
        out = parse(files.v_header(124, cs=0x73524742, profile_size=1234))
        self.assertIsNone(out["profile_bytes"])


class Os2Tests(unittest.TestCase):
    def test_core_header(self):
        bmp = files.os2_core(bpp=8, width=4, height=2,
                             rows=[bytes([0, 0, 0, 0]), bytes([1, 0, 0, 0])])
        out = parse(bmp)
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]),
                         ("core", 4, 2, 8))
        self.assertEqual(out["compression"], "rgb")
        self.assertEqual(out["palette_colors"], 256)
        self.assertEqual(out["row_bytes"], 4)
        # RGBTRIPLE palette: index 1 = (b=1,g=2,r=3)
        self.assertEqual(out["corner_pixel"], "#030201")

    def test_os2v2_rgb_triplet_palette(self):
        # The bug: 4-byte entries were assumed, shifting every colour after #0
        bmp = files.os2_v2(width=4, height=1, rows=[bytes([1, 0, 0, 0])])
        out = parse(bmp)
        self.assertEqual(out["header"], "os2v2")
        self.assertEqual(out["palette_colors"], 256)
        self.assertEqual(out["corner_pixel"], "#030201")

    def test_os2v2_compression_names(self):
        for code, name in ((0, "rgb"), (1, "rle8"), (2, "rle4"),
                           (3, "huffman1d"), (4, "rle24")):
            out = parse(files.os2_v2(comp=code))
            self.assertEqual(out["compression"], name)

    def test_os2v2_bitfields_three_masks(self):
        # OS/2 compression code 6 = BRU_BITFIELDS (Windows' code 3 is HUFFMAN1D here)
        row = struct.pack("<H", 0xF800) + bytes(6)
        bmp = files.os2_v2(bpp=16, comp=6, height=1, rows=[row])
        out = parse(bmp)
        self.assertEqual(out["compression"], "bitfields")
        self.assertEqual(out["masks"], ["0000f800", "000007e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertEqual(out["corner_pixel"], "#ff0000")


if __name__ == "__main__":
    unittest.main()

