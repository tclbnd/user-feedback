import struct
import unittest

from gemkit.img import ImgError, parse


def gem_header(words, planes, patlen, width, height):
    return struct.pack(">8H", 1, words, planes, patlen, 85, 85, width, height)


class BasicTests(unittest.TestCase):
    def test_black_bar(self):
        head = struct.pack(">8H", 1, 8, 1, 2, 85, 85, 16, 1)
        out = parse(head + bytes([0x81, 0x01]))
        self.assertEqual(out["first_row"], "1111111100000000")

    def test_not_an_image(self):
        with self.assertRaises(ImgError):
            parse(b"\x00\x01")

    def test_truncated_is_incomplete(self):
        head = gem_header(8, 1, 2, 16, 2)
        out = parse(head + bytes([0x80, 0x02, 0xFF, 0x00]))
        self.assertEqual(out["rows"], 1)
        self.assertFalse(out["complete"])


class VerticalRepeatTests(unittest.TestCase):
    def test_repeat_previous_line(self):
        # Repeat records have count 0 and still carry patlen unused pattern bytes.
        data = gem_header(8, 1, 2, 16, 3)
        data += bytes([0x80, 0x02, 0xF0, 0x00])
        data += bytes([0x00, 0x00, 0x00, 0x00])
        data += bytes([0x00, 0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual((out["rows"], out["complete"]), (3, True))
        self.assertEqual(out["first_row"], "1111000000000000")
        self.assertEqual(out["histogram"], [36, 12])

    def test_repeat_after_repeat(self):
        data = gem_header(8, 1, 2, 8, 4)
        data += bytes([0x80, 0x01, 0x00])
        data += bytes([0x00, 0x00, 0x00, 0x00]) * 3
        out = parse(data)
        self.assertEqual(out["rows"], 4)
        self.assertEqual(out["histogram"], [32, 0])

    def test_repeat_with_long_pattern(self):
        # patlen 3: the repeat record still occupies 2 + 3 bytes.
        data = gem_header(8, 1, 3, 24, 2)
        data += bytes([0x00, 0x01, 0xAA, 0x55, 0xAA])
        data += bytes([0x00, 0x00, 0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual((out["rows"], out["complete"]), (2, True))
        self.assertEqual(out["first_row"], "101010100101010110101010")
        self.assertEqual(out["histogram"], [24, 24])


class PatternLengthTests(unittest.TestCase):
    def test_pattern_length_one(self):
        data = gem_header(8, 1, 1, 8, 2)
        data += bytes([0x00, 0x01, 0xFF])
        data += bytes([0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual((out["rows"], out["complete"]), (2, True))
        self.assertEqual(out["first_row"], "11111111")
        self.assertEqual(out["histogram"], [0, 16])

    def test_pattern_repeated_multiple_times(self):
        data = gem_header(8, 1, 4, 16, 2)
        data += bytes([0x00, 0x02, 0x80, 0x40, 0x00, 0x00])
        data += bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual(out["rows"], 2)
        self.assertEqual(out["histogram"], [28, 4])


class HeaderLengthTests(unittest.TestCase):
    def test_longer_header(self):
        # Header length is in words; the scan lines start after it.
        data = gem_header(10, 1, 2, 16, 1) + bytes([0x12, 0x34, 0x56, 0x78])
        data += bytes([0x81, 0x01])
        out = parse(data)
        self.assertEqual(out["first_row"], "1111111100000000")

    def test_too_short_header_is_rejected(self):
        data = gem_header(6, 1, 2, 16, 1) + bytes([0x81, 0x01])
        with self.assertRaises(ImgError):
            parse(data)


class MultiPlaneTests(unittest.TestCase):
    def test_two_planes(self):
        data = gem_header(8, 2, 2, 16, 1)
        data += bytes([0x80, 0x04, 0xFF, 0x00, 0x00, 0xFF])
        out = parse(data)
        self.assertEqual(out["first_row"], "1111111122222222")
        self.assertEqual(out["histogram"], [0, 8, 8, 0])

    def test_four_planes(self):
        data = gem_header(8, 4, 2, 4, 1)
        data += bytes([0x80, 0x04, 0xF0, 0xF0, 0xF0, 0xF0])
        out = parse(data)
        # Every plane's byte 0 is 0xF0: pixels 0-3 set in all four planes,
        # giving index 0b1111 = 15 for each of them.
        self.assertEqual(out["first_row"], "ffff")
        self.assertEqual(out["histogram"][15], 4)
        self.assertEqual(len(out["histogram"]), 16)

    def test_width_not_multiple_of_eight(self):
        data = gem_header(8, 2, 2, 10, 1)
        data += bytes([0x80, 0x04, 0xFF, 0xC0, 0x00, 0x00])
        out = parse(data)
        self.assertEqual(out["histogram"], [0, 10, 0, 0])


class PaletteTests(unittest.TestCase):
    def make(self, planes, colours):
        words = 8 + 2 + 1 + 3 * len(colours)
        head = gem_header(words, planes, 2, 8, 1)
        ext = b"XIMG" + struct.pack(">H", 0)
        for r, g, b in colours:
            ext += struct.pack(">3H", r, g, b)
        lb = 1
        data = bytes([0x00, 0x01] + [0xFF] * lb)
        return parse(head + ext + data)

    def test_four_colour_palette(self):
        out = self.make(2, [(0, 0, 0), (1000, 0, 0), (0, 1000, 0), (500, 500, 500)])
        self.assertEqual(out["palette"], ["#000000", "#ff0000", "#00ff00", "#808080"])

    def test_sixteen_colour_palette(self):
        colours = [(i * 1000 // 15,) * 3 for i in range(16)]
        out = self.make(4, colours)
        self.assertEqual(len(out["palette"]), 16)
        self.assertEqual(out["palette"][0], "#000000")
        self.assertEqual(out["palette"][15], "#ffffff")

    def test_palette_ignored_when_flag_set(self):
        words = 13
        head = gem_header(words, 2, 2, 8, 1)
        ext = b"XIMG" + struct.pack(">H", 1) + b"\x00" * 12
        out = parse(head + ext + bytes([0x00, 0x01, 0xFF]))
        self.assertIsNone(out["palette"])

    def test_no_palette_without_ximg(self):
        head = gem_header(8, 2, 2, 8, 1)
        out = parse(head + bytes([0x00, 0x01, 0xFF]))
        self.assertIsNone(out["palette"])


class DamagedTests(unittest.TestCase):
    def test_truncated_stream_never_raises(self):
        base = gem_header(8, 2, 2, 16, 4) + bytes([0x80, 0x04, 0xFF, 0x00, 0x00, 0x00])
        for cut in range(16, len(base)):
            out = parse(base[:cut])
            self.assertLessEqual(out["rows"], 4)
            self.assertEqual(len(out["histogram"]), 4)

    def test_ragged_pattern_record_never_raises(self):
        data = gem_header(8, 1, 7, 8, 2) + bytes([0x00, 0x05, 0xAA])
        out = parse(data)
        self.assertIn("rows", out)

    def test_huge_run_is_clipped_to_line(self):
        data = gem_header(8, 1, 2, 8, 2) + bytes([0xFF, 0x00, 0x00, 0x00, 0x00])
        out = parse(data)
        self.assertEqual((out["rows"], out["complete"]), (2, True))
        self.assertEqual(out["histogram"], [0, 16])
