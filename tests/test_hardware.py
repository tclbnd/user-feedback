import unittest

from shrkit.shr import parse

SCB = 0x7D00
PAL = 0x7E00


def screen():
    return bytearray(32768)


def put_word(buf, offset, word):
    buf[offset] = word & 0xFF
    buf[offset + 1] = word >> 8


def put_palette(buf, number, colors):
    for i, word in enumerate(colors):
        put_word(buf, PAL + 32 * number + 2 * i, word)


def fill_scbs(buf, value):
    """Set all 200 scan-line control bytes (unused lines default to zero)."""
    for y in range(200):
        buf[SCB + y] = value


class PaletteTests(unittest.TestCase):
    def test_color_word_little_endian_components(self):
        buf = screen()
        # palette 0 entry 5 = 0x0ABC -> R=A, G=B, B=C
        put_palette(buf, 0, [0] * 5 + [0x0ABC])
        buf[0] = 0x50  # 320 mode, no fill: high nibble 5 at columns 0-1
        out = parse(buf)
        self.assertEqual(out["points"]["top_left"], "#aabbcc")

    def test_palettes_8_to_15_selectable(self):
        buf = screen()
        fill_scbs(buf, 11)  # every 320-mode line selects palette 11
        put_palette(buf, 11, [0] * 3 + [0x0F0F])  # entry 3 of palette 11
        buf[0] = 0x30
        out = parse(buf)
        self.assertEqual(out["palettes_used"], [11])
        self.assertEqual(out["points"]["top_left"], "#ff00ff")


class Mode320Tests(unittest.TestCase):
    def test_pixel_pair_left_right_order(self):
        buf = screen()
        put_palette(buf, 0, [0x0100, 0x0200, 0x0300, 0x0400])
        buf[0] = 0x21  # high nibble 2 (left), low nibble 1 (right)
        out = parse(buf)
        self.assertEqual(out["points"]["top_left"], "#330000")  # col 0: nibble 2 -> entry 2
        self.assertEqual(out["points"]["x1"], "#330000")       # same 320 pixel
        self.assertEqual(out["points"]["x2"], "#220000")       # col 2: nibble 1 -> entry 1
        self.assertEqual(out["points"]["x3"], "#220000")

    def test_fill_mode_stretches_even_pixel(self):
        buf = screen()
        put_palette(buf, 0, [0x0000, 0x0900, 0x0A00, 0x0B00])
        buf[SCB] = 0x20  # 320 mode + fill, palette 0
        buf[0] = 0x21  # high nibble 2 stretched; low nibble ignored
        out = parse(buf)
        self.assertEqual(out["fill_lines"], 1)
        for key in ("top_left", "x1", "x2", "x3"):
            self.assertEqual(out["points"][key], "#aa0000")

    def test_fill_bit_ignored_in_640_mode(self):
        buf = screen()
        fill_scbs(buf, 0x80 | 0x20)  # every line 640 mode with fill bit also set
        put_palette(buf, 0, [0x0100, 0x0200, 0x0300, 0x0400])
        buf[0] = 0xE4  # 2-bit pixels: 3,2,1,0
        out = parse(buf)
        self.assertEqual(out["modes"], {"320": 0, "640": 200})
        self.assertEqual(out["fill_lines"], 0)
        self.assertEqual(out["points"]["top_left"], "#440000")  # index 3 -> entry 3
        self.assertEqual(out["points"]["x1"], "#330000")        # index 2 -> entry 2
        self.assertEqual(out["points"]["x2"], "#220000")        # index 1 -> entry 1
        self.assertEqual(out["points"]["x3"], "#110000")        # index 0 -> entry 0


class Mode640Tests(unittest.TestCase):
    def test_four_pixels_per_byte_use_entries_0_to_3(self):
        buf = screen()
        put_palette(buf, 0, [0x0100, 0x0200, 0x0300, 0x0400])
        buf[SCB] = 0x80  # 640 mode
        buf[0] = 0x1D  # bits: 00 01 11 01 -> indices 0,1,3,1
        out = parse(buf)
        self.assertEqual(out["points"]["top_left"], "#110000")
        self.assertEqual(out["points"]["x1"], "#220000")
        self.assertEqual(out["points"]["x2"], "#440000")
        self.assertEqual(out["points"]["x3"], "#220000")

    def test_mixed_modes_per_line(self):
        buf = screen()
        put_palette(buf, 0, [0x0100, 0x0200, 0x0300, 0x0400])
        buf[SCB] = 0x80  # line 0: 640
        buf[SCB + 1] = 0x00  # line 1: 320
        buf[0] = 0xE4  # line 0: indices 3,2,1,0
        buf[160] = 0x32  # line 1: pixels 3 then 2 (doubled)
        out = parse(buf)
        self.assertEqual(out["modes"], {"320": 199, "640": 1})
        self.assertEqual(out["points"]["top_left"], "#440000")  # 640 index 3 -> entry 3
        self.assertEqual(out["points"]["row_1"], "#440000")     # 320 nibble 3 -> entry 3


class StatsTests(unittest.TestCase):
    def test_interrupt_and_palette_stats(self):
        buf = screen()
        fill_scbs(buf, 5)  # default every line to palette 5
        buf[SCB] = 0x40 | 5  # interrupt + palette 5
        buf[SCB + 1] = 0x80 | 9  # 640 + palette 9
        out = parse(buf)
        self.assertEqual(out["interrupt_lines"], 1)
        self.assertEqual(out["palettes_used"], [5, 9])
        self.assertEqual(out["modes"], {"320": 199, "640": 1})

    def test_distinct_colors_count(self):
        buf = screen()
        put_palette(buf, 0, [0x0100, 0x0200, 0x0300])
        buf[0] = 0x10  # high nibble 1, low nibble 0
        buf[1] = 0x20  # high nibble 2, low nibble 0
        out = parse(buf)
        self.assertEqual(out["colors"], 3)


if __name__ == "__main__":
    unittest.main()
