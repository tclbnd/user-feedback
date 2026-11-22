import unittest

from dpxkit.dpx import DpxError, line_bytes, parse

from frames import dpx


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(dpx(width=100, height=20, creator="Film Lab"))
        self.assertEqual((out["endian"], out["version"], out["creator"], out["orientation"]), ("big", "V2.0", "Film Lab", 0))

    def test_rgb(self):
        el = parse(dpx(width=100, height=20))["elements"][0]
        self.assertEqual((el["descriptor"], el["bit_depth"], el["packing"]), ("RGB", 10, "filled_a"))


class EndianTests(unittest.TestCase):
    def test_little_endian(self):
        out = parse(dpx(width=100, height=20, endian="little"))
        self.assertEqual(out["endian"], "little")
        self.assertEqual((out["width"], out["height"]), (100, 20))
        self.assertEqual(out["elements"][0]["data_bytes"], 8000)

    def test_not_dpx(self):
        with self.assertRaises(DpxError):
            parse(b"XXXX" + bytes(2044))


class SizeTests(unittest.TestCase):
    def test_filled_10bit_pads_to_words(self):
        # 100 RGB pixels is 300 samples: 100 32-bit words exactly.
        self.assertEqual(line_bytes(100, 3, 10, 1), 400)

    def test_packing_changes_line_size(self):
        self.assertEqual(line_bytes(101, 3, 10, 1), 404)
        self.assertEqual(line_bytes(101, 3, 10, 0), 380)

    def test_end_of_line_padding(self):
        self.assertEqual(line_bytes(100, 3, 10, 1, eol=16), 416)

    def test_element_size(self):
        el = parse(dpx(width=256, height=194))["elements"][0]
        self.assertEqual(el["data_bytes"], 198656)

    def test_rle_has_no_size(self):
        el = parse(dpx(elements=((50, 10, 1, 1),)))["elements"][0]
        self.assertTrue(el["rle"])
        self.assertIsNone(el["data_bytes"])

    def test_truncated(self):
        self.assertTrue(parse(dpx(width=100, height=20)[:2080])["truncated"])
        self.assertFalse(parse(dpx(width=100, height=20))["truncated"])


class DescriptorTests(unittest.TestCase):
    def test_422(self):
        el = parse(dpx(width=100, height=10, elements=((100, 10, 1, 0),)))["elements"][0]
        self.assertEqual((el["descriptor"], el["bit_depth"], el["data_bytes"]), ("CbYCrY", 10, 2680))

    def test_4224(self):
        el = parse(dpx(width=100, height=10, elements=((103, 10, 1, 0),)))["elements"][0]
        self.assertEqual((el["descriptor"], el["data_bytes"]), ("CbYCrA", 5360))

    def test_all_descriptors_named(self):
        for code, name in ((1, "R"), (2, "G"), (3, "B"), (4, "A"), (6, "Y"), (50, "RGB"),
                           (51, "RGBA"), (52, "ABGR"), (100, "CbYCrY"), (101, "CbYACrYA"),
                           (102, "CbYCr"), (103, "CbYCrA")):
            el = parse(dpx(elements=((code, 10, 1, 0),)))["elements"][0]
            self.assertEqual(el["descriptor"], name)


class RateAndTimecodeTests(unittest.TestCase):
    def test_film_rate_wins(self):
        self.assertEqual(parse(dpx(film_rate=24.0, tv_rate=30.0))["frame_rate"], 24.0)

    def test_tv_rate_when_film_undefined(self):
        self.assertEqual(parse(dpx(film_rate=None, tv_rate=25.0))["frame_rate"], 25.0)

    def test_no_rate_is_null(self):
        self.assertIsNone(parse(dpx(film_rate=None))["frame_rate"])

    def test_timecode_is_bcd(self):
        self.assertEqual(parse(dpx(timecode=(12, 34, 56, 12)))["timecode"], "12:34:56:12")

    def test_timecode_zero(self):
        self.assertEqual(parse(dpx(timecode=(0, 0, 0, 0)))["timecode"], "00:00:00:00")


class UndefinedFieldTests(unittest.TestCase):
    def test_undefined_dimensions(self):
        out = parse(dpx(width=0xFFFFFFFF, height=0xFFFFFFFF, elements=()))
        self.assertEqual((out["width"], out["height"]), (None, None))
        self.assertEqual((out["display_width"], out["display_height"]), (None, None))
