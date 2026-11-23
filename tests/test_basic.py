import struct
import unittest

from dpxkit.dpx import line_bytes, parse

from frames import dpx


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(dpx(width=100, height=20, creator="Film Lab"))
        self.assertEqual((out["endian"], out["version"], out["creator"], out["orientation"]), ("big", "V2.0", "Film Lab", 0))

    def test_rgb(self):
        el = parse(dpx(width=100, height=20))["elements"][0]
        self.assertEqual((el["descriptor"], el["bit_depth"], el["packing"]), ("RGB", 10, "filled_a"))


class EndianTests(unittest.TestCase):
    def test_little_endian_magic(self):
        out = parse(dpx(width=240, height=135, endian="little"))
        self.assertEqual(out["endian"], "little")

    def test_little_endian_values(self):
        out = parse(dpx(width=240, height=135, film_rate=25.0, endian="little"))
        self.assertEqual((out["width"], out["height"], out["frame_rate"]), (240, 135, 25.0))

    def test_little_endian_timecode(self):
        out = parse(dpx(timecode=(10, 23, 45, 12), endian="little"))
        self.assertEqual(out["timecode"], "10:23:45:12")

    def test_not_dpx(self):
        with self.assertRaises(ValueError):
            parse(b"NOPE" + bytes(2044))


class SizeTests(unittest.TestCase):
    def test_full_size_fields(self):
        out = parse(dpx(width=2048, height=1556))
        self.assertEqual((out["width"], out["height"]), (2048, 1556))
        self.assertEqual((out["display_width"], out["display_height"]), (2048, 1556))

    def test_transposed_orientation(self):
        frame = bytearray(dpx(width=100, height=20))
        struct.pack_into(">H", frame, 768, 5)
        out = parse(bytes(frame))
        self.assertEqual((out["display_width"], out["display_height"]), (20, 100))

    def test_data_bytes_filled_10(self):
        el = parse(dpx(width=100, height=20))["elements"][0]
        # 3 components per 32-bit word, 300 samples -> 100 words per line.
        self.assertEqual(el["data_bytes"], 400 * 20)

    def test_data_bytes_packed_10(self):
        el = parse(dpx(width=100, height=20, elements=((50, 10, 0, 0),)))["elements"][0]
        # 300 samples at 10 bits take 375 bytes, padded to a 32-bit word.
        self.assertEqual(el["data_bytes"], 376 * 20)

    def test_data_bytes_8_bit(self):
        el = parse(dpx(width=101, height=20, elements=((6, 8, 0, 0),)))["elements"][0]
        self.assertEqual(el["data_bytes"], 104 * 20)

    def test_data_bytes_with_line_padding(self):
        frame = bytearray(dpx(width=100, height=20))
        struct.pack_into(">I", frame, 780 + 32, 6)
        el = parse(bytes(frame))["elements"][0]
        self.assertEqual(el["data_bytes"], 406 * 20)

    def test_line_bytes(self):
        self.assertEqual(line_bytes(2048, 3, 10, 1), 8192)
        self.assertEqual(line_bytes(2048, 3, 10, 0), 7680)
        self.assertEqual(line_bytes(1920, 2, 10, 1), 5120)
        self.assertEqual(line_bytes(720, 3, 8, 1), 2160)
        self.assertEqual(line_bytes(2048, 3, 16, 2), 12288)

    def test_truncated(self):
        frame = dpx(width=100, height=20)
        self.assertTrue(parse(frame[:-1])["truncated"])

    def test_rle_has_no_size(self):
        el = parse(dpx(width=100, height=20, elements=((50, 10, 1, 1),)))["elements"][0]
        self.assertTrue(el["rle"])
        self.assertIsNone(el["data_bytes"])


class FilmAndTelevisionTests(unittest.TestCase):
    def test_film_rate_preferred(self):
        out = parse(dpx(film_rate=24.0, tv_rate=25.0))
        self.assertEqual(out["frame_rate"], 24.0)

    def test_tv_rate_when_film_undefined(self):
        self.assertEqual(parse(dpx(film_rate=None, tv_rate=25.0))["frame_rate"], 25.0)

    def test_undefined_rate_is_null(self):
        self.assertIsNone(parse(dpx(film_rate=None, tv_rate=None))["frame_rate"])

    def test_timecode_bcd(self):
        self.assertEqual(parse(dpx(timecode=(14, 52, 37, 18)))["timecode"], "14:52:37:18")
        self.assertEqual(parse(dpx(timecode=(1, 2, 3, 4)))["timecode"], "01:02:03:04")

    def test_timecode_undefined(self):
        self.assertIsNone(parse(dpx(timecode=None))["timecode"])

    def test_interlaced(self):
        self.assertIsNone(parse(dpx())["interlaced"])
        self.assertFalse(parse(dpx(interlace=0))["interlaced"])
        self.assertTrue(parse(dpx(interlace=1))["interlaced"])

    def test_sequence(self):
        frame = bytearray(dpx())
        struct.pack_into(">II", frame, 1712, 120, 480)
        out = parse(bytes(frame))
        self.assertEqual((out["frame_position"], out["sequence_length"]), (120, 480))


class DescriptorTests(unittest.TestCase):
    def test_422(self):
        el = parse(dpx(width=240, height=135, elements=((102, 10, 1, 0),)))["elements"][0]
        self.assertEqual(el["descriptor"], "CbYCr")

    def test_all_descriptors(self):
        expected = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y", 50: "RGB", 51: "RGBA", 52: "ABGR",
                    100: "CbYCrY", 101: "CbYACrYA", 102: "CbYCr", 103: "CbYCrA"}
        for code, name in expected.items():
            el = parse(dpx(elements=((code, 8, 0, 0),)))["elements"][0]
            self.assertEqual(el["descriptor"], name)


class UndefinedFieldTests(unittest.TestCase):
    def test_no_room_for_industry_headers(self):
        # A file that reserves no room for the industry header has no film or
        # television fields at all.
        frame = bytearray(dpx(width=100, height=20, film_rate=None, timecode=None, data=False))
        struct.pack_into(">I", frame, 28, 0)
        out = parse(bytes(frame[:2048]))
        self.assertIsNone(out["frame_position"])
        self.assertIsNone(out["frame_rate"])
        self.assertIsNone(out["timecode"])
        self.assertIsNone(out["interlaced"])

    def test_pixel_aspect(self):
        out = parse(dpx(width=100, height=20, pixel_aspect=(4, 3)))
        self.assertEqual(out["pixel_aspect"], "4/3")

    def test_undefined_numeric_fields(self):
        frame = bytearray(dpx(width=100, height=20))
        struct.pack_into(">I", frame, 1628, 0xFFFFFFFF)
        struct.pack_into(">I", frame, 1632, 0xFFFFFFFF)
        struct.pack_into(">I", frame, 1712, 0xFFFFFFFF)
        out = parse(bytes(frame))
        self.assertIsNone(out["pixel_aspect"])
        self.assertIsNone(out["frame_position"])

    def test_text_fields(self):
        frame = bytearray(dpx(width=100, height=20, stamp=None, creator=""))
        frame[136:160] = b"\xff" * 24
        out = parse(bytes(frame))
        self.assertIsNone(out["created"])
        self.assertIsNone(out["creator"])

    def test_created(self):
        out = parse(dpx(stamp="2024:03:15:10:22:05:UTC"))
        self.assertEqual(out["created"], "2024-03-15T10:22:05")

    def test_padding_eol_undefined(self):
        frame = bytearray(dpx(width=100, height=20))
        struct.pack_into(">I", frame, 780 + 32, 0xFFFFFFFF)
        el = parse(bytes(frame))["elements"][0]
        self.assertEqual(el["data_bytes"], 400 * 20)
