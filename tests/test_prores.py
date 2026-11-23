import struct
import unittest

from proreskit.prores import ProresError, parse

from streams import frame, stream


class FrameWalkingTests(unittest.TestCase):
    def test_many_frames_are_all_found(self):
        # Regression: the frame size includes the 4-byte size field, so frames
        # are back-to-back; skipping size+4 lost every frame after the first.
        for n in (2, 3, 10, 25):
            with self.subTest(n=n):
                out = parse(stream(n))
                self.assertEqual(out["frames"], n)
                self.assertFalse(out["truncated"])
                self.assertEqual(out["inconsistent_frames"], 0)

    def test_truncated_mid_frame_is_flagged_not_counted(self):
        full = stream(3)
        first_size = struct.unpack_from(">I", full, 0)[0]
        cut = first_size + 40  # second frame header present, payload cut short
        out = parse(full[:cut])
        self.assertEqual(out["frames"], 1)
        self.assertTrue(out["truncated"])

    def test_truncated_in_frame_header(self):
        full = stream(2)
        first_size = struct.unpack_from(">I", full, 0)[0]
        out = parse(full[:first_size + 3])
        self.assertEqual(out["frames"], 1)
        self.assertTrue(out["truncated"])

    def test_no_frames_raises(self):
        with self.assertRaises(ProresError):
            parse(b"")

    def test_bad_magic_raises(self):
        with self.assertRaises(ProresError):
            parse(b"\x00\x00\x00\x08xxxx")


class HeaderCodeTests(unittest.TestCase):
    def test_chroma_422(self):
        out = parse(stream(1, chroma=2))
        self.assertEqual(out["chroma"], "4:2:2")

    def test_chroma_444(self):
        out = parse(stream(1, chroma=3))
        self.assertEqual(out["chroma"], "4:4:4")

    def test_interlaced_has_two_pictures(self):
        tff = parse(stream(1, scan=1))
        self.assertEqual(tff["scan"], "interlaced_tff")
        self.assertEqual(tff["pictures"], 2)
        self.assertEqual(tff["slices"], 16)  # two pictures, eight slices each
        bff = parse(stream(1, scan=2))
        self.assertEqual(bff["scan"], "interlaced_bff")
        self.assertEqual(bff["pictures"], 2)

    def test_progressive_has_one_picture(self):
        out = parse(stream(1, scan=0))
        self.assertEqual(out["scan"], "progressive")
        self.assertEqual(out["pictures"], 1)
        self.assertEqual(out["slices"], 8)

    def test_frame_rate_codes(self):
        # SMPTE RDD 36 code table, including the fractional NTSC rates.
        for code, label in ((1, "23.976"), (2, "24"), (3, "25"), (4, "29.97"),
                            (5, "30"), (6, "50"), (7, "59.94"), (8, "60")):
            with self.subTest(code=code):
                self.assertEqual(parse(stream(1, rate=code))["frame_rate"], label)

    def test_unknown_rate_is_null(self):
        self.assertIsNone(parse(stream(1, rate=0))["frame_rate"])

    def test_aspect_and_color(self):
        out = parse(stream(1, aspect=2, color=(9, 16, 9)))
        self.assertEqual(out["aspect"], "4:3")
        self.assertEqual(out["color"], [9, 16, 9])

    def test_alpha_bits(self):
        self.assertIsNone(parse(stream(1, alpha=0))["alpha_bits"])
        self.assertEqual(parse(stream(1, alpha=1))["alpha_bits"], 8)
        self.assertEqual(parse(stream(1, alpha=2))["alpha_bits"], 16)

    def test_creator_from_first_frame(self):
        out = parse(stream(2, creator=b"arri"))
        self.assertEqual(out["creator"], "arri")


class ConsistencyTests(unittest.TestCase):
    def test_custom_quant_matrices_keep_pictures_aligned(self):
        # A custom matrix enlarges the frame header; pictures start after it.
        out = parse(stream(2, matrices=3))
        self.assertTrue(out["custom_matrices"])
        self.assertEqual(out["inconsistent_frames"], 0)
        self.assertEqual(out["frames"], 2)

    def test_bad_slice_table_marks_frame_inconsistent(self):
        good = frame()
        bad = frame(bad_slice=True)
        out = parse(good + bad + good)
        self.assertEqual(out["frames"], 3)
        self.assertEqual(out["inconsistent_frames"], 1)
        self.assertFalse(out["truncated"])

    def test_frame_size_stats(self):
        small = parse(stream(2, slices=4))
        large = parse(stream(2, slices=12))
        self.assertLess(small["min_frame_bytes"], large["min_frame_bytes"])
        self.assertEqual(small["min_frame_bytes"], small["max_frame_bytes"])


if __name__ == "__main__":
    unittest.main()
