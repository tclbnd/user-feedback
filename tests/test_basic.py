import struct
import unittest

from shrkit.shr import ShrError, parse


def screen():
    return bytearray(32768)


def put_palette(data, palette, words):
    for color, word in enumerate(words):
        struct.pack_into("<H", data, 0x7E00 + 32 * palette + 2 * color, word)


def put_line(data, y, scb, pixels):
    data[0x7D00 + y] = scb
    data[y * 160:y * 160 + len(pixels)] = pixels


def ramp():
    # Palette entry i holds word i, so expectations can name the entry directly.
    return list(range(16))


class BasicTests(unittest.TestCase):
    def test_blank(self):
        out = parse(bytes(32768))
        self.assertEqual((out["modes"], out["colors"]), ({"320": 200, "640": 0}, 1))

    def test_wrong_size(self):
        with self.assertRaises(ShrError):
            parse(bytes(32000))


class ModeTests(unittest.TestCase):
    def test_320_high_nibble_is_left_pixel(self):
        data = screen()
        put_palette(data, 0, ramp())
        put_line(data, 0, 0x00, bytes([0x21]))
        out = parse(bytes(data))
        self.assertEqual(out["points"]["top_left"], "#000022")
        self.assertEqual(out["points"]["x1"], "#000022")
        self.assertEqual(out["points"]["x2"], "#000011")
        self.assertEqual(out["points"]["x3"], "#000011")

    def test_fill_mode_uses_high_nibble_for_four_columns(self):
        data = screen()
        put_palette(data, 0, ramp())
        put_line(data, 0, 0x20, bytes([0x37]))
        out = parse(bytes(data))
        self.assertEqual((out["points"]["top_left"], out["points"]["x1"],
                          out["points"]["x2"], out["points"]["x3"]),
                         ("#000033", "#000033", "#000033", "#000033"))
        self.assertEqual(out["fill_lines"], 1)
        self.assertEqual(out["modes"], {"320": 200, "640": 0})

    def test_640_pixel_groups(self):
        data = screen()
        put_palette(data, 0, ramp())
        put_line(data, 0, 0x80, bytes([0x1B]))  # pixels 0, 1, 2, 3
        out = parse(bytes(data))
        self.assertEqual((out["points"]["top_left"], out["points"]["x1"],
                          out["points"]["x2"], out["points"]["x3"]),
                         ("#000000", "#000055", "#0000aa", "#0000ff"))
        self.assertEqual(out["modes"], {"320": 199, "640": 1})
        self.assertEqual(out["fill_lines"], 0)

    def test_modes_mix_by_line(self):
        data = screen()
        put_palette(data, 0, ramp())
        put_line(data, 0, 0x20, bytes([0x10]))
        put_line(data, 1, 0x80, bytes([0x00]))
        out = parse(bytes(data))
        self.assertEqual(out["modes"], {"320": 199, "640": 1})
        self.assertEqual(out["fill_lines"], 1)


class ColourTests(unittest.TestCase):
    def test_palette_word_is_little_endian_0rgb(self):
        data = screen()
        put_palette(data, 0, [0] * 16)
        struct.pack_into("<H", data, 0x7E00, 0x0ABC)
        put_line(data, 0, 0x00, bytes([0x00]))
        out = parse(bytes(data))
        self.assertEqual(out["points"]["top_left"], "#aabbcc")

    def test_palette_above_seven(self):
        data = screen()
        put_palette(data, 0, [0] * 16)
        put_palette(data, 9, [0x0123] * 16)
        put_line(data, 0, 0x09, bytes([0x00]))
        out = parse(bytes(data))
        self.assertEqual(out["points"]["top_left"], "#112233")
        self.assertIn(9, out["palettes_used"])
