import struct
import unittest

from dpxkit.dpx import DpxError, line_bytes, parse

from frames import dpx


class EndianTests(unittest.TestCase):
    def test_little_endian_windows_frame(self):
        out = parse(dpx(width=240, height=135, elements=((100, 10, 1, 0),), endian="little"))
        self.assertEqual(out["endian"], "little")
        self.assertEqual((out["width"], out["height"]), (240, 135))
        self.assertEqual(out["elements"][0]["descriptor"], "CbYCrY")

    def test_bad_magic(self):
        with self.assertRaises(DpxError):
            parse(b"XXXX" + dpx(width=8, height=4)[4:])

    def test_short_header(self):
        with self.assertRaises(DpxError):
            parse(dpx(width=8, height=4)[:1024])


class GeometryTests(unittest.TestCase):
    def test_32bit_dimensions(self):
        out = parse(dpx(width=4096, height=3112))
        self.assertEqual((out["width"], out["height"]), (4096, 3112))

    def test_undefined_dimensions(self):
        frame = bytearray(dpx(width=8, height=4))
        struct.pack_into(">II", frame, 772, 0xFFFFFFFF, 0xFFFFFFFF)
        out = parse(bytes(frame))
        self.assertIsNone(out["width"])
        self.assertIsNone(out["height"])
        self.assertIsNone(out["display_width"])
        self.assertIsNone(out["display_height"])
        self.assertIsNone(out["elements"][0]["data_bytes"])

    def test_orientation_transpose(self):
        frame = bytearray(dpx(width=8, height=4))
        struct.pack_into(">H", frame, 768, 5)
        out = parse(bytes(frame))
        self.assertEqual((out["display_width"], out["display_height"]), (4, 8))

    def test_orientation_undefined(self):
        frame = bytearray(dpx(width=8, height=4))
        struct.pack_into(">H", frame, 768, 0xFFFF)
        self.assertIsNone(parse(bytes(frame))["orientation"])

    def test_pixel_aspect_reduced(self):
        frame = bytearray(dpx(width=8, height=4))
        struct.pack_into(">II", frame, 1628, 160, 120)
        self.assertEqual(parse(bytes(frame))["pixel_aspect"], "4/3")

    def test_pixel_aspect_undefined(self):
        self.assertIsNone(parse(dpx(width=8, height=4))["pixel_aspect"])


class FrameRateTests(unittest.TestCase):
    def test_nan_film_rate_falls_back_to_tv(self):
        out = parse(dpx(width=8, height=4, film_rate=None, tv_rate=25.0))
        self.assertEqual(out["frame_rate"], 25.0)

    def test_film_rate_takes_precedence(self):
        out = parse(dpx(width=8, height=4, film_rate=24.0, tv_rate=25.0))
        self.assertEqual(out["frame_rate"], 24.0)

    def test_both_rates_undefined(self):
        out = parse(dpx(width=8, height=4, film_rate=None, tv_rate=None))
        self.assertIsNone(out["frame_rate"])

    def test_no_film_header_uses_tv_rate(self):
        out = parse(dpx(width=8, height=4, film_len=0, tv_rate=25.0))
        self.assertEqual(out["frame_rate"], 25.0)
        self.assertIsNone(out["frame_position"])
        self.assertIsNone(out["sequence_length"])


class TimecodeTests(unittest.TestCase):
    def test_bcd_timecode(self):
        out = parse(dpx(width=8, height=4, timecode=(14, 52, 37, 18)))
        self.assertEqual(out["timecode"], "14:52:37:18")

    def test_flag_bits_are_masked(self):
        frame = bytearray(dpx(width=8, height=4, timecode=(14, 0, 0, 0)))
        frame[1920] = 0x94  # bit 7 is the hours color-frame flag; BCD hours stay 14
        self.assertEqual(parse(bytes(frame))["timecode"], "14:00:00:00")
        frame[1923] = 0x80  # bit 7 of the frames byte is the frame color-frame flag
        self.assertEqual(parse(bytes(frame))["timecode"], "14:00:00:00")
        frame[1923] = 0x40  # bit 6 is the drop-frame flag
        self.assertEqual(parse(bytes(frame))["timecode"], "14:00:00:00")

    def test_undefined_timecode(self):
        out = parse(dpx(width=8, height=4, timecode=None))
        self.assertIsNone(out["timecode"])

    def test_no_tv_header(self):
        out = parse(dpx(width=8, height=4, tv_len=0))
        self.assertIsNone(out["timecode"])
        self.assertIsNone(out["interlaced"])

    def test_interlace_codes(self):
        frame = bytearray(dpx(width=8, height=4))
        frame[1928] = 1
        self.assertTrue(parse(bytes(frame))["interlaced"])
        frame[1928] = 0
        self.assertFalse(parse(bytes(frame))["interlaced"])
        frame[1928] = 0xFF
        self.assertIsNone(parse(bytes(frame))["interlaced"])


class ElementTests(unittest.TestCase):
    def test_422_descriptors(self):
        for desc, name in ((100, "CbYCrY"), (101, "CbYCr"), (102, "CbYCrA"), (103, "CbYACrYA")):
            el = parse(dpx(width=16, height=8, elements=((desc, 10, 1, 0),)))["elements"][0]
            self.assertEqual(el["descriptor"], name)

    def test_unknown_descriptor(self):
        el = parse(dpx(width=8, height=4, elements=((77, 8, 0, 0),)))["elements"][0]
        self.assertEqual(el["descriptor"], "type_77")

    def test_packings_and_padding(self):
        self.assertEqual(line_bytes(10, 3, 10, 0), 40)     # packed: ceil(300/32)*4
        self.assertEqual(line_bytes(10, 3, 10, 1), 40)     # filled A: ceil(30/3)*4
        self.assertEqual(line_bytes(10, 3, 10, 2), 40)     # filled B: ceil(3/4)*4 per pixel
        self.assertEqual(line_bytes(10, 3, 12, 1), 60)
        self.assertEqual(line_bytes(10, 3, 12, 2), 80)     # ceil(3/2)*4 per pixel
        self.assertEqual(line_bytes(10, 2, 10, 2), 40)     # 4:2:2 CbYCrY
        self.assertEqual(line_bytes(10, 3, 8, 0), 32)      # packed lines pad to a 32-bit word
        self.assertEqual(line_bytes(10, 3, 16, 0), 60)
        self.assertEqual(line_bytes(10, 3, 10, 1, eol=6), 46)

    def test_data_bytes_all_packings(self):
        for packing, pitch in ((0, 40), (1, 40), (2, 40)):
            el = parse(dpx(width=10, height=5, elements=((50, 10, packing, 0),)))["elements"][0]
            self.assertEqual(el["data_bytes"], pitch * 5)

    def test_rle_has_no_data_size(self):
        el = parse(dpx(width=10, height=5, elements=((50, 10, 1, 1),)))["elements"][0]
        self.assertTrue(el["rle"])
        self.assertIsNone(el["data_bytes"])

    def test_truncated(self):
        frame = dpx(width=100, height=20)
        self.assertTrue(parse(frame[:-100])["truncated"])
        self.assertFalse(parse(frame)["truncated"])

    def test_multiple_elements(self):
        frame = dpx(width=16, height=8, elements=((50, 10, 1, 0), (6, 16, 0, 0)))
        els = parse(frame)["elements"]
        self.assertEqual([e["descriptor"] for e in els], ["RGB", "Y"])
        self.assertEqual([e["data_bytes"] for e in els], [512, 256])
        self.assertEqual(els[1]["offset"], 2048 + 512)

    def test_transfer_and_colorimetric_tables(self):
        frame = bytearray(dpx(width=8, height=4))
        b = 780
        frame[b + 21], frame[b + 22] = 0, 0
        el = parse(bytes(frame))["elements"][0]
        self.assertEqual(el["transfer"], "user_defined")
        self.assertEqual(el["colorimetric"], "user_defined")
        frame[b + 21], frame[b + 22] = 12, 12
        el = parse(bytes(frame))["elements"][0]
        self.assertEqual(el["transfer"], "z_homogeneous")
        self.assertEqual(el["colorimetric"], "type_12")
        frame[b + 21], frame[b + 22] = 42, 99
        el = parse(bytes(frame))["elements"][0]
        self.assertEqual((el["transfer"], el["colorimetric"]), ("type_42", "type_99"))

    def test_signed_and_encrypted(self):
        frame = bytearray(dpx(width=8, height=4))
        frame[780] = 1
        struct.pack_into(">I", frame, 660, 1234)
        out = parse(bytes(frame))
        self.assertTrue(out["elements"][0]["signed"])
        self.assertTrue(out["encrypted"])


if __name__ == "__main__":
    unittest.main()
