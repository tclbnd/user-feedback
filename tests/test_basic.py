import unittest

from bmpkit.bmp import parse

import files
from files import tiny


class BasicTests(unittest.TestCase):
    def test_size(self):
        out = parse(tiny())
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]), ("info", 4, 1, 24))

    def test_corner(self):
        self.assertEqual(parse(tiny())["corner_pixel"], "#ff0000")

    def test_ppm_to_dpi(self):
        self.assertEqual(parse(tiny())["dpi"], [72, 72])

    def test_dpi_96(self):
        self.assertEqual(parse(files.info(1, 1, 24, xppm=3780, yppm=3780))["dpi"], [96, 96])

    def test_row_padding_to_four_bytes(self):
        out = parse(files.info(3, 2, 24, pixels=b"\0" * 24))
        self.assertEqual(out["row_bytes"], 12)
        self.assertEqual(out["image_bytes"], 24)

    def test_one_bit_row_padding(self):
        # 9 pixels at 1 bpp is 2 stored bytes, padded to 4.
        out = parse(files.info(9, 3, 1, used=2, palette=files.entries((0, 0, 0), (255, 255, 255)),
                               pixels=b"\0" * 12))
        self.assertEqual((out["row_bytes"], out["image_bytes"]), (4, 12))

    def test_four_bit_row_padding(self):
        # 3 pixels at 4 bpp is 2 stored bytes, padded to 4.
        out = parse(files.info(3, 2, 4, used=16, palette=files.entries((0, 0, 0)) * 16,
                               pixels=b"\0" * 8))
        self.assertEqual((out["row_bytes"], out["image_bytes"]), (4, 8))

    def test_compressed_size_comes_from_header(self):
        out = parse(files.info(8, 8, 8, comp=1, image_size=17, pixels=b"\0" * 40))
        self.assertEqual((out["row_bytes"], out["image_bytes"]), (None, 17))

    def test_v3_header(self):
        out = parse(files.v3(1, 1, 24, pixels=b"\0" * 4))
        self.assertEqual(out["header"], "v3")
        self.assertEqual(out["row_bytes"], 4)

    def test_palette_count_defaults_to_full_table(self):
        out = parse(files.info(1, 1, 8, used=0, palette=files.entries((0, 0, 0)) * 256))
        self.assertEqual(out["palette_colors"], 256)
        self.assertEqual(parse(files.info(1, 1, 4, used=0, palette=files.entries((0, 0, 0)) * 16))["palette_colors"], 16)
        self.assertEqual(parse(files.info(1, 1, 1, used=0, palette=files.entries((0, 0, 0)) * 2))["palette_colors"], 2)

    def test_palette_count_is_honoured(self):
        out = parse(files.info(1, 1, 8, used=16, palette=files.entries((0, 0, 0)) * 16))
        self.assertEqual(out["palette_colors"], 16)


class TopDownTests(unittest.TestCase):
    def test_negative_height_is_top_down(self):
        # 1 x 2, 24 bpp: first stored row is green, second red.
        out = parse(files.info(1, -2, 24, pixels=bytes([0, 255, 0]) + b"\0" + bytes([0, 0, 255]) + b"\0"))
        self.assertEqual((out["height"], out["top_down"]), (2, True))
        self.assertEqual(out["corner_pixel"], "#00ff00")

    def test_bottom_up_reads_last_row(self):
        out = parse(files.info(1, 2, 24, pixels=bytes([0, 255, 0]) + b"\0" + bytes([0, 0, 255]) + b"\0"))
        self.assertEqual((out["height"], out["top_down"]), (2, False))
        self.assertEqual(out["corner_pixel"], "#ff0000")


class PalettePixelTests(unittest.TestCase):
    def test_one_bit_pixel(self):
        # 8 x 1: bits 1000 0000 -> index 1 for the leftmost pixel.
        palette = files.entries((0, 0, 0), (10, 20, 30))
        out = parse(files.info(8, 1, 1, palette=palette, pixels=b"\x80\0\0\0", used=2))
        self.assertEqual(out["corner_pixel"], "#0a141e")

    def test_one_bit_pixel_second(self):
        palette = files.entries((0, 0, 0), (10, 20, 30))
        out = parse(files.info(8, 1, 1, palette=palette, pixels=b"\x40\0\0\0", used=2))
        self.assertEqual(out["corner_pixel"], "#000000")

    def test_four_bit_pixel(self):
        palette = files.entries((0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)) + files.entries((9, 9, 9)) * 13
        # 2 x 1: high nibble 3, low nibble 1.
        out = parse(files.info(2, 1, 4, palette=palette, pixels=b"\x31\0\0\0", used=16))
        self.assertEqual(out["corner_pixel"], "#0000ff")

    def test_four_bit_palette_offset_after_masks(self):
        palette = files.entries((0, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 0)) + files.entries((9, 9, 9)) * 12
        # 2 x 1: high nibble 1, low nibble 2; palette index 1 is green.
        out = parse(files.info(2, 1, 4, comp=3, masks=(0x0F00, 0x00F0, 0x000F),
                               palette=palette, pixels=b"\x12\0\0\0", used=16))
        self.assertEqual(out["corner_pixel"], "#00ff00")


class DefaultMaskTests(unittest.TestCase):
    def test_sixteen_bit_rgb_default_is_555(self):
        out = parse(files.info(1, 1, 16, pixels=b"\x00\x7c" + b"\0\0"))
        self.assertEqual(out["masks"], ["00007c00", "000003e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_thirty_two_bit_rgb_default_ignores_top_byte(self):
        # Stored bytes are blue, green, red, unused; the top byte must not leak into red.
        out = parse(files.info(1, 1, 32, pixels=b"\x00\x00\xff\xff"))
        self.assertEqual(out["masks"], ["00ff0000", "0000ff00", "000000ff", "00000000"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_v2_header_has_three_masks(self):
        out = parse(files.v2(1, 1, 16, masks=(0x7C00, 0x03E0, 0x001F), pixels=b"\x00\x7c" + b"\0\0"))
        self.assertEqual(out["header"], "v2")
        self.assertEqual(out["masks"], ["00007c00", "000003e0", "0000001f", "00000000"])
        self.assertFalse(out["alpha"])
        self.assertIsNone(out["color_space"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_v4_masks_are_read_from_header(self):
        out = parse(files.v4(1, 1, 16, masks=(0x0F00, 0x00F0, 0x000F, 0),
                             pixels=b"\x00\x0f" + b"\0\0"))
        self.assertEqual(out["masks"], ["00000f00", "000000f0", "0000000f", "00000000"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_bitfields_alpha_within_header(self):
        out = parse(files.v5(1, 1, 32, comp=6, masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
                             pixels=b"\x00\x00\xff\x00"))
        self.assertTrue(out["alpha"])
        self.assertEqual(out["masks"][3], "ff000000")
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_bitfields_masks_after_info_header(self):
        out = parse(files.info(1, 1, 16, comp=3, masks=(0x7C00, 0x03E0, 0x001F),
                               pixels=b"\x00\x7c" + b"\0\0"))
        self.assertEqual(out["masks"], ["00007c00", "000003e0", "0000001f", "00000000"])
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_alpha_bitfields_has_fourth_mask(self):
        out = parse(files.info(1, 1, 32, comp=6, masks=(0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
                               pixels=b"\x00\x00\xff\x80"))
        self.assertTrue(out["alpha"])
        self.assertEqual(out["masks"][3], "ff000000")

    def test_rgb_has_no_masks(self):
        self.assertIsNone(parse(tiny())["masks"])


class ColorSpaceTests(unittest.TestCase):
    def test_info_header_has_no_colour_space(self):
        out = parse(tiny())
        self.assertIsNone(out["color_space"])
        self.assertIsNone(out["profile_bytes"])

    def test_v4_srgb(self):
        self.assertEqual(parse(files.v4(1, 1, 24, pixels=b"\0\0\0\0"))["color_space"], "sRGB")

    def test_v4_windows(self):
        self.assertEqual(parse(files.v4(1, 1, 24, space=0x57696E20, pixels=b"\0" * 4))["color_space"], "windows")

    def test_v4_calibrated(self):
        self.assertEqual(parse(files.v4(1, 1, 24, space=0, pixels=b"\0" * 4))["color_space"], "calibrated")

    def test_unknown_colour_space(self):
        self.assertEqual(parse(files.v4(1, 1, 24, space=0x12345678, pixels=b"\0" * 4))["color_space"],
                         "type_12345678")

    def test_v5_embedded_profile(self):
        out = parse(files.v5(1, 1, 24, space=0x4D424544, profile=4096, pixels=b"\0" * 4))
        self.assertEqual(out["color_space"], "embedded")
        self.assertEqual(out["profile_bytes"], 4096)

    def test_v5_linked_profile(self):
        out = parse(files.v5(1, 1, 24, space=0x4C494E4B, profile=512, pixels=b"\0" * 4))
        self.assertEqual(out["color_space"], "linked")
        self.assertEqual(out["profile_bytes"], 512)

    def test_v4_has_no_profile_size(self):
        self.assertIsNone(parse(files.v4(1, 1, 24, pixels=b"\0" * 4))["profile_bytes"])


class Os2Tests(unittest.TestCase):
    def test_core_header(self):
        palette = files.entries3((0, 0, 0), (255, 0, 0))
        out = parse(files.core(8, 1, 1, palette=palette, pixels=b"\x80\0\0\0"))
        self.assertEqual((out["header"], out["width"], out["height"], out["bit_count"]), ("core", 8, 1, 1))
        self.assertEqual(out["palette_colors"], 2)
        self.assertEqual(out["row_bytes"], 4)
        self.assertFalse(out["top_down"])
        self.assertEqual(out["dpi"], [0, 0])
        self.assertEqual(out["compression"], "rgb")
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_core_24_bit(self):
        out = parse(files.core(2, 1, 24, pixels=bytes([0, 0, 255, 255, 0, 0]) + b"\0\0"))
        self.assertEqual(out["row_bytes"], 8)
        self.assertEqual(out["corner_pixel"], "#ff0000")

    def test_os2v2_compression_names(self):
        self.assertEqual(parse(files.os2v2(1, 1, 8, comp=3))["compression"], "huffman1d")
        self.assertEqual(parse(files.os2v2(1, 1, 8, comp=4))["compression"], "rle24")
        self.assertEqual(parse(files.os2v2(1, 1, 8))["compression"], "rgb")

    def test_os2v2_units_in_inches(self):
        self.assertEqual(parse(files.os2v2(1, 1, 24, units=0))["dpi"], [72, 72])
        self.assertEqual(parse(files.os2v2(1, 1, 24, units=1, res=96))["dpi"], [96, 96])

    def test_os2v2_palette(self):
        palette = files.entries3((0, 0, 0), (1, 2, 3)) + files.entries3((9, 9, 9)) * 14
        out = parse(files.os2v2(2, 1, 4, palette=palette, pixels=b"\x10\0\0\0"))
        self.assertEqual(out["palette_colors"], 16)
        self.assertEqual(out["corner_pixel"], "#010203")
