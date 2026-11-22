import unittest

from dpxkit.dpx import parse

from frames import dpx


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(dpx(width=100, height=20, creator="Film Lab"))
        self.assertEqual((out["endian"], out["version"], out["creator"], out["orientation"]), ("big", "V2.0", "Film Lab", 0))

    def test_rgb(self):
        el = parse(dpx(width=100, height=20))["elements"][0]
        self.assertEqual((el["descriptor"], el["bit_depth"], el["packing"]), ("RGB", 10, "filled_a"))


class EndianTests(unittest.TestCase):
    def test_little_endian_workstation_frame(self):
        out = parse(dpx(width=1920, height=1080, endian="<", creator="Windows"))
        self.assertEqual(out["endian"], "little")
        self.assertEqual((out["width"], out["height"], out["creator"]), (1920, 1080, "Windows"))

    def test_unknown_magic(self):
        from dpxkit.dpx import DpxError
        with self.assertRaises(DpxError):
            parse(b"NOPE" + bytes(2100))


class SizeTests(unittest.TestCase):
    def test_width_height_are_32_bit(self):
        out = parse(dpx(width=70000, height=40000, data=False))
        self.assertEqual((out["width"], out["height"]), (70000, 40000))

    def test_data_bytes_filled_10bit_rgb(self):
        # 3 components packed three per 32-bit word: ceil(w*3/3)*4 per line.
        el = parse(dpx(width=2048, height=1556))["elements"][0]
        self.assertEqual(el["data_bytes"], 2048 * 4 * 1556)

    def test_data_bytes_honours_line_padding(self):
        el = parse(dpx(width=100, height=20, eol=16))["elements"][0]
        self.assertEqual(el["data_bytes"], (100 * 4 + 16) * 20)

    def test_data_bytes_packed(self):
        # packed 10-bit is bit-contiguous and padded to whole words.
        el = parse(dpx(width=100, height=20, elements=((50, 10, 0, 0),)))["elements"][0]
        self.assertEqual(el["packing"], "packed")
        self.assertEqual(el["data_bytes"], (100 * 3 * 10 // 32 + 1) * 4 * 20)

    def test_rle_has_no_data_bytes(self):
        el = parse(dpx(elements=((50, 10, 1, 1),)))["elements"][0]
        self.assertIsNone(el["data_bytes"])
        self.assertTrue(el["rle"])

    def test_truncated(self):
        self.assertTrue(parse(dpx(width=100, height=20, cut=40))["truncated"])


class VideoDescriptorTests(unittest.TestCase):
    def test_422_descriptors(self):
        for desc, name, comps in ((100, "CbYCrY", 2), (102, "CbYCr", 3)):
            el = parse(dpx(width=720, height=486, elements=((desc, 10, 1, 0),)))["elements"][0]
            self.assertEqual(el["descriptor"], name)
            self.assertEqual(el["data_bytes"], __import__("math").ceil(720 * comps / 3) * 4 * 486)

    def test_all_descriptors(self):
        names = {1: "R", 2: "G", 3: "B", 4: "A", 6: "Y", 50: "RGB", 51: "RGBA",
                 52: "ABGR", 100: "CbYCrY", 101: "CbYACrYA", 102: "CbYCr", 103: "CbYCrA"}
        for desc, name in names.items():
            el = parse(dpx(width=32, height=4, elements=((desc, 10, 1, 0),)))["elements"][0]
            self.assertEqual(el["descriptor"], name)


class FrameRateTests(unittest.TestCase):
    def test_film_rate(self):
        self.assertEqual(parse(dpx(film_rate=24.0))["frame_rate"], 24.0)

    def test_undefined_film_rate_is_null(self):
        self.assertIsNone(parse(dpx(film_rate=None))["frame_rate"])

    def test_film_rate_wins_over_tv_rate(self):
        self.assertEqual(parse(dpx(film_rate=24.0, tv_rate=25.0))["frame_rate"], 24.0)

    def test_tv_rate_when_film_rate_undefined(self):
        self.assertEqual(parse(dpx(film_rate=None, tv_rate=25.0))["frame_rate"], 25.0)


class TimecodeTests(unittest.TestCase):
    def test_bcd_timecode(self):
        self.assertEqual(parse(dpx(timecode=(20, 52, 55, 24)))["timecode"], "20:52:55:24")

    def test_undefined_timecode(self):
        self.assertIsNone(parse(dpx(timecode=None))["timecode"])

    def test_invalid_bcd_is_null(self):
        import struct
        data = bytearray(dpx(timecode=None))
        struct.pack_into(">I", data, 1920, 0x20825524)   # 0x82 is not BCD
        self.assertIsNone(parse(bytes(data))["timecode"])


class UndefinedFieldTests(unittest.TestCase):
    def test_undefined_header_fields_are_null(self):
        out = parse(dpx(film_rate=None, timecode=None, stamp=None, creator=""))
        self.assertIsNone(out["frame_rate"])
        self.assertIsNone(out["timecode"])
        self.assertIsNone(out["created"])
        self.assertIsNone(out["creator"])
        self.assertIsNone(out["interlaced"])

