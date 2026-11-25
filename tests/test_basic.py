import os
import struct
import unittest

from gemkit.img import ImgError, parse


class BasicTests(unittest.TestCase):
    def test_black_bar(self):
        head = struct.pack(">8H", 1, 8, 1, 2, 85, 85, 16, 1)
        out = parse(head + bytes([0x81, 0x01]))
        self.assertEqual(out["first_row"], "1111111100000000")

    def test_not_an_image(self):
        with self.assertRaises(ImgError):
            parse(b"\x00\x01")


def header(words=8, planes=1, patlen=2, width=16, height=1):
    return struct.pack(">8H", 1, words, planes, patlen, 85, 85, width, height)


class ScanLineTests(unittest.TestCase):
    def test_vertical_repeat(self):
        # Line 0 is a literal, then 00 00 repeats it.  The patlen bytes that
        # follow a repeat are padding (here DE AD / BE EF) and are skipped.
        data = header(height=4) + bytes([0x80, 0x02, 0x12, 0x34])
        data += bytes([0x00, 0x00, 0xDE, 0xAD]) + bytes([0x80, 0x02, 0xAA, 0xBB])
        data += bytes([0x00, 0x00, 0xBE, 0xEF])
        out = parse(data)
        self.assertEqual(out["rows"], 4)
        self.assertTrue(out["complete"])
        # 12 34 -> 00010010 00110100 (5 set pixels); AA BB has 10 set.
        self.assertEqual(out["first_row"], "0001001000110100")
        self.assertEqual(out["histogram"], [34, 30])

    def test_pattern_length_is_honoured(self):
        # patlen 4: one pattern record repeats four bytes as a unit.
        data = header(planes=1, patlen=4, width=32, height=2)
        data += bytes([0x00, 0x01, 0x0F, 0x0F, 0x0F, 0x0F])
        data += bytes([0x00, 0x00, 0xAA, 0xAA, 0xAA, 0xAA])
        out = parse(data)
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["first_row"], "0000111100001111" * 2)
        self.assertEqual(out["histogram"], [32, 32])

    def test_pattern_repeat(self):
        data = header(width=32, height=1) + bytes([0x00, 0x02, 0xAA, 0x55])
        out = parse(data)
        self.assertEqual(out["first_row"], "1010101001010101" * 2)

    def test_long_header(self):
        # The raster starts after the declared header length, not at byte 16.
        data = header(words=9, width=8, height=1) + b"\xAA\xBB" + bytes([0x81])
        out = parse(data)
        self.assertEqual(out["rows"], 1)
        self.assertEqual(out["first_row"], "11111111")
        self.assertIsNone(out["palette"])

    def test_truncated(self):
        data = header(height=3) + bytes([0x80, 0x02, 0xFF, 0xFF, 0x80, 0x01])
        out = parse(data)
        self.assertEqual(out["rows"], 1)
        self.assertFalse(out["complete"])


class ColorTests(unittest.TestCase):
    def test_two_planes(self):
        # plane 0 = 0x0F, plane 1 = 0x33; index = plane1:plane0
        data = header(planes=2, width=8, height=1) + bytes([0x80, 0x02, 0x0F, 0x33])
        out = parse(data)
        self.assertEqual(out["planes"], 2)
        self.assertEqual(out["first_row"], "00221133")
        self.assertEqual(out["histogram"], [2, 2, 2, 2])

    def test_ximg_palette(self):
        words = 8 + 2 + 1 + 12
        data = header(words=words, planes=2, width=8, height=1)
        data += b"XIMG" + struct.pack(">H", 0)
        data += struct.pack(">12H", 0, 0, 0, 1000, 0, 0, 0, 1000, 0, 1000, 1000, 1000)
        data += bytes([0x80, 0x02, 0x55, 0x55])
        out = parse(data)
        self.assertEqual(out["palette"], ["#000000", "#ff0000", "#00ff00", "#ffffff"])
        # both planes are 01010101, so each pixel is 0b11 or 0b00
        self.assertEqual(out["first_row"], "03030303")
        self.assertEqual(out["histogram"], [4, 0, 0, 4])

    def test_ximg_default_palette_flag(self):
        words = 8 + 2 + 1
        data = header(words=words, planes=1, width=8, height=1)
        data += b"XIMG" + struct.pack(">H", 1) + bytes([0x81])
        out = parse(data)
        self.assertIsNone(out["palette"])
        self.assertEqual(out["rows"], 1)


class ExampleFileTests(unittest.TestCase):
    """The two images shipped with the project are known-good grammar samples."""

    def read(self, name):
        path = os.path.join(os.path.dirname(__file__), os.pardir, "examples", name)
        with open(path, "rb") as fh:
            return parse(fh.read())

    def test_logo(self):
        out = self.read("logo.img")
        self.assertEqual((out["width"], out["height"], out["planes"]), (32, 6, 1))
        self.assertEqual((out["rows"], out["complete"]), (6, True))
        self.assertIsNone(out["palette"])
        self.assertEqual(out["first_row"], "1" * 32)
        self.assertEqual(out["histogram"], [104, 88])

    def test_chart(self):
        out = self.read("chart.img")
        self.assertEqual((out["width"], out["height"], out["planes"]), (16, 3, 2))
        self.assertEqual((out["rows"], out["complete"]), (3, True))
        self.assertEqual(out["palette"], ["#000000", "#ff0000", "#00ff00", "#ffffff"])
        self.assertEqual(out["first_row"], "3333222222223333")
        self.assertEqual(out["histogram"], [8, 0, 24, 16])


class ExtraTests(unittest.TestCase):
    def test_eight_planes(self):
        width = 4
        # one byte per plane; high nibble holds the four pixels
        line = bytes([0xF0, 0x50, 0x30, 0x10, 0x00, 0x00, 0x00, 0x00])
        data = header(planes=8, patlen=4, width=width, height=1)
        data += bytes([0x80, len(line)]) + line
        out = parse(data)
        hist = [0] * 256
        for v in (1, 3, 5, 15):
            hist[v] = 1
        self.assertEqual(out["histogram"], hist)
        self.assertEqual(out["first_row"], "135f")

    def test_odd_width_second_plane(self):
        # width 3 in two planes: each plane row is one byte, high bits used.
        data = header(planes=2, width=3, height=1)
        data += bytes([0x80, 0x02, 0x80, 0x40])  # plane0=100, plane1=010
        out = parse(data)
        self.assertEqual(out["first_row"], "120")

    def test_pattern_length_eight(self):
        data = header(patlen=8, width=64, height=1)
        data += bytes([0x00, 0x01]) + bytes(range(8))
        out = parse(data)
        self.assertEqual(out["rows"], 1)

    def test_repeated_vertical_repeat(self):
        data = header(width=8, height=3) + bytes([0x81])
        data += bytes([0x00, 0x00, 0x00, 0x00])
        data += bytes([0x00, 0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual(out["rows"], 3)
        self.assertEqual(out["histogram"], [0, 24])

    def test_zero_height(self):
        out = parse(header(height=0))
        self.assertEqual(out["rows"], 0)
        self.assertTrue(out["complete"])
        self.assertIsNone(out["first_row"])
