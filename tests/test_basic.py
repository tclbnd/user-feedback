import struct
import unittest

from timkit.tim import TimError, parse


def _tim(bpp, body, w, h=1, clut=None, cx=0, cy=0, cw=0, ch=0):
    mode = {4: 0, 8: 1, 16: 2, 24: 3}[bpp]
    flags = mode | (8 if clut is not None else 0)
    data = struct.pack("<II", 0x10, flags)
    if clut is not None:
        data += struct.pack("<I4H", 12 + len(clut), cx, cy, cw, ch) + clut
    return data + struct.pack("<I4H", 12 + len(body), 0, 0, w, h) + body


class BasicTests(unittest.TestCase):
    def test_grey_16bit_texel(self):
        word = 16 << 10 | 16 << 5 | 16
        data = struct.pack("<II", 0x10, 2) + struct.pack("<I4H", 14, 0, 0, 1, 1) + struct.pack("<H", word)
        out = parse(data)
        self.assertEqual(out["first_row"], ["#848484"])
        self.assertTrue(out["complete"])

    def test_not_a_tim(self):
        with self.assertRaises(TimError):
            parse(b"\x11\x00\x00\x00" + bytes(20))

    def test_16bit_red_is_low_bits(self):
        word = 31                       # blue 0, green 0, red 31
        out = parse(_tim(16, struct.pack("<H", word), 1))
        self.assertEqual(out["first_row"], ["#ff0000"])
        self.assertEqual(out["last_pixel"], "#ff0000")
        out = parse(_tim(16, struct.pack("<H", 31 << 10), 1))
        self.assertEqual(out["first_row"], ["#0000ff"])

    def test_16bit_transparency(self):
        words = [0x0000, 0x8000 | 31 << 10, 31, 0x8000]
        out = parse(_tim(16, struct.pack("<4H", *words), 4))
        self.assertEqual(out["first_row"], [None, "#0000ff", "#ff0000", "#000000"])
        self.assertEqual(out["transparent_pixels"], 1)
        self.assertEqual(out["semi_transparent_pixels"], 2)
        self.assertEqual(out["colors_used"], 3)
        self.assertEqual(out["last_pixel"], "#000000")

    def test_4bit_low_nibble_first(self):
        clut = struct.pack("<4H", 0x0000, 31, 31 << 5, 31 << 10)
        out = parse(_tim(4, struct.pack("<H", 0x3201), 1, clut=clut, cw=4, ch=1))
        self.assertEqual(out["bpp"], 4)
        self.assertEqual(out["width"], 4)
        self.assertEqual(out["first_row"], ["#ff0000", None, "#00ff00", "#0000ff"])
        self.assertEqual(out["clut"], {"x": 0, "y": 0, "colors": 4, "palettes": 1})
        self.assertEqual(out["transparent_pixels"], 1)

    def test_8bit_palette_and_palettes_count(self):
        clut = struct.pack("<4H", 31, 31 << 5, 31 << 10, 0x8000)
        out = parse(_tim(8, bytes([0, 1, 2, 3] * 2), 2, h=2, clut=clut, cw=4, ch=1))
        self.assertEqual(out["width"], 4)
        self.assertEqual(out["rows"], 2)
        # four entries only: part of the first 8-bit palette
        self.assertEqual(out["clut"]["colors"], 4)
        self.assertEqual(out["clut"]["palettes"], 1)
        self.assertEqual(out["first_row"], ["#ff0000", "#00ff00", "#0000ff", "#000000"])
        self.assertEqual(out["semi_transparent_pixels"], 2)
        self.assertEqual(out["last_pixel"], "#000000")
        full = struct.pack("<512H", *([31] * 512))
        out = parse(_tim(8, bytes([0, 255]), 2, clut=full, cw=256, ch=2))
        self.assertEqual(out["clut"], {"x": 0, "y": 0, "colors": 256, "palettes": 2})

    def test_8bit_without_clut(self):
        out = parse(_tim(8, bytes([0, 1]), 1))
        self.assertIsNone(out["clut"])
        self.assertEqual(out["first_row"], [None, None])
        self.assertEqual(out["transparent_pixels"], 2)

    def test_24bit_width_and_order(self):
        # image width counts 16-bit words; each pixel is three bytes stored
        # blue, green, red, so three words hold two pixels
        body = bytes([10, 20, 30, 40, 50, 60])
        out = parse(_tim(24, body, 3))
        self.assertEqual(out["bpp"], 24)
        self.assertEqual(out["width"], 2)
        self.assertEqual(out["first_row"], ["#1e140a", "#3c3228"])
        self.assertEqual(out["transparent_pixels"], 0)
        self.assertEqual(out["semi_transparent_pixels"], 0)
        self.assertEqual(out["colors_used"], 2)
        self.assertEqual(out["last_pixel"], "#3c3228")

    def test_24bit_truncated(self):
        out = parse(_tim(24, bytes([1, 2, 3, 4]), 3, h=3))
        self.assertEqual(out["width"], 2)
        self.assertEqual(out["rows"], 0)
        self.assertFalse(out["complete"])
        self.assertEqual(out["height"], 3)

    def test_24bit_rows_and_padding(self):
        # one pixel per row, the row padded to a 16-bit word (4 bytes)
        body = bytes([1, 2, 3, 0]) * 2
        out = parse(_tim(24, body, 2, h=2))
        self.assertEqual(out["rows"], 2)
        self.assertTrue(out["complete"])
        self.assertEqual(out["colors_used"], 1)
        self.assertEqual(out["last_pixel"], "#030201")

    def test_clut_entry_zero_is_transparent(self):
        clut = struct.pack("<4H", 31, 0x0000, 31 << 5, 31 << 10)
        out = parse(_tim(4, bytes([0x01, 0x02]), 1, clut=clut, cw=2, ch=2))
        self.assertEqual(out["width"], 4)
        self.assertEqual(out["first_row"], [None, "#ff0000", "#00ff00", "#ff0000"])
        self.assertEqual(out["transparent_pixels"], 1)
        self.assertEqual(out["semi_transparent_pixels"], 0)

    def test_first_row_stops_at_eight(self):
        out = parse(_tim(16, struct.pack("<10H", *([31] * 10)), 10))
        self.assertEqual(len(out["first_row"]), 8)
        self.assertEqual(out["last_pixel"], "#ff0000")
