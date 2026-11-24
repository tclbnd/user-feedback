import unittest

from pcxkit.pcx import parse

import images


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(images.tiny())
        self.assertEqual((out["version"], out["rle"], out["height"], out["dpi"]), (5, False, 1, [72, 72]))

    def test_lines(self):
        out = parse(images.tiny())
        self.assertEqual((out["decoded_lines"], out["palette"]), (1, "none"))


class GeometryTests(unittest.TestCase):
    def test_width_counts_the_window_inclusively(self):
        out = parse(images.tiny())
        self.assertEqual((out["width"], out["height"]), (2, 1))

    def test_offset_window(self):
        out = parse(images.offset_window())
        self.assertEqual((out["width"], out["height"]), (10, 1))
        self.assertEqual((out["decoded_lines"], out["complete"]), (1, True))


class RleTests(unittest.TestCase):
    def test_literal_high_bytes_are_not_run_markers(self):
        out = parse(images.literal_high_bytes())
        self.assertEqual((out["decoded_lines"], out["complete"]), (1, True))
        self.assertEqual((out["runs"], out["corner_pixel"]), (0, "#ffffff"))

    def test_run_crosses_line_boundary(self):
        out = parse(images.run_across_lines())
        self.assertEqual((out["decoded_lines"], out["complete"]), (2, True))
        self.assertEqual((out["runs"], out["corner_pixel"]), (1, "#010203"))


class PaletteTests(unittest.TestCase):
    def test_vga_palette_is_not_scaled(self):
        palette = bytearray(768)
        palette[3:6] = bytes([200, 100, 50])
        out = parse(images.vga256(palette))
        self.assertEqual((out["palette"], out["corner_pixel"]), ("vga256", "#c86432"))

    def test_truecolor_planes_are_separate(self):
        out = parse(images.truecolor((0x11, 0x22, 0x33)))
        self.assertEqual((out["palette"], out["corner_pixel"]), ("truecolor", "#112233"))

    def test_ega_plane_zero_is_the_low_bit(self):
        palette = bytearray(48)
        palette[15:18] = bytes([10, 20, 30])
        out = parse(images.ega16(palette))
        self.assertEqual((out["palette"], out["corner_pixel"]), ("ega16", "#0a141e"))
