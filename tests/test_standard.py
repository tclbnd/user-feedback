"""Regression tests for the 13818-2 / 11172-2 parsing fixes."""
import unittest
from fractions import Fraction

from m2vkit.stream import summary

from streams import disp_ext, mpeg1, mpeg2, pce, seq, seq_ext


class Mpeg2RateTests(unittest.TestCase):
    def test_ntsc_frame_rate_is_fractional(self):
        # frame_rate_code 4 = 30000/1001 (29.97), never rounded to 30.
        data = mpeg2(seq(720, 480, 3, 4, 8000) + seq_ext(),
                     [("I", {})] + [("P", {})] * 4)
        out = summary(data)
        self.assertEqual(out["frame_rate"], "30000/1001")
        self.assertEqual(Fraction(out["duration"]),
                         5 / Fraction(30000, 1001))

    def test_frame_rate_extension(self):
        # rate = base * (n + 1) / (d + 1): 25 * 2/1 = 50 fps.
        data = mpeg2(seq(352, 288, 2, 3, 100) + seq_ext(n=1, d=0),
                     [("I", {})] * 4)
        out = summary(data)
        self.assertEqual(out["frame_rate"], "50/1")
        self.assertEqual(Fraction(out["duration"]), Fraction(4, 50))


class AspectTests(unittest.TestCase):
    def test_mpeg2_sar_derived_from_display_size(self):
        # 720x480 coded, 704x480 displayed, 16:9 DAR (news_ntsc layout).
        header = seq(720, 480, 3, 4, 8000) + seq_ext(chroma=2) + \
            disp_ext(704, 480)
        data = mpeg2(header, [[("I", {"structure": 1}),
                               ("I", {"structure": 2})]])
        out = summary(data)
        self.assertEqual((out["width"], out["height"]), (720, 480))
        self.assertEqual((out["display_width"], out["display_height"]),
                         (704, 480))
        self.assertEqual(out["dar"], "16:9")
        expected_sar = Fraction(16, 9) * Fraction(480, 704)
        self.assertEqual(out["sar"], "%d:%d" % (
            expected_sar.numerator, expected_sar.denominator))

    def test_mpeg2_without_display_extension_uses_coded_size(self):
        data = mpeg2(seq(720, 576, 2, 3, 3750) + seq_ext(),
                     [("I", {})])
        out = summary(data)
        self.assertEqual((out["display_width"], out["display_height"]),
                         (720, 576))
        self.assertEqual(out["dar"], "4:3")
        self.assertEqual(out["sar"], "16:15")

    def test_reserved_aspect_code_is_null(self):
        data = mpeg2(seq(352, 240, 0, 5, 100) + seq_ext(),
                     [("I", {})])
        out = summary(data)
        self.assertIsNone(out["dar"])
        self.assertIsNone(out["sar"])


class FieldAndTimingTests(unittest.TestCase):
    def test_field_pictures_count_individually_but_pair_into_frames(self):
        frames = [[("I", {"structure": 1}), ("P", {"structure": 2})]]
        frames += [[("B", {"structure": 1}), ("B", {"structure": 2})]
                   for _ in range(3)]
        data = mpeg2(seq(720, 480, 3, 4, 8000) + seq_ext(), frames)
        out = summary(data)
        self.assertEqual(out["pictures"]["I"], 1)
        self.assertEqual(out["pictures"]["P"], 1)
        self.assertEqual(out["pictures"]["B"], 6)
        self.assertEqual(sum(out["pictures"].values()), 8)
        self.assertEqual(out["frames"], 4)
        # Four frames at 30000/1001.
        self.assertEqual(Fraction(out["duration"]),
                         4 / Fraction(30000, 1001))

    def test_repeat_first_field_frame_picture_adds_a_field_period(self):
        # 3:2 pulldown on film: every frame picture carries rff=1, so each
        # displayed frame lasts 3 field periods instead of 2.
        data = mpeg2(seq(720, 480, 3, 4, 8000) + seq_ext(),
                     [("I", {"rff": 1})] + [("P", {"rff": 1})] * 3)
        out = summary(data)
        self.assertEqual(out["frames"], 4)
        self.assertEqual(Fraction(out["duration"]),
                         Fraction(4 * 3, 2) / Fraction(30000, 1001))

    def test_repeat_first_field_field_picture(self):
        # Repeated top field: two field pictures, first one repeated.
        frames = [[("I", {"structure": 1, "rff": 1}),
                   ("P", {"structure": 2})]]
        data = mpeg2(seq(720, 480, 3, 7, 8000) + seq_ext(), frames)
        out = summary(data)
        self.assertEqual(out["frames"], 1)
        # field periods = (1 + 1) + 1 = 3.
        self.assertEqual(Fraction(out["duration"]),
                         Fraction(3, 2) / Fraction(60000, 1001))

    def test_mixed_frame_and_field_pictures(self):
        frames = [("I", {}),
                  [("P", {"structure": 1}), ("B", {"structure": 2})]]
        data = mpeg2(seq(352, 240, 2, 5, 100) + seq_ext(), frames)
        out = summary(data)
        self.assertEqual(sum(out["pictures"].values()), 3)
        self.assertEqual(out["frames"], 2)
        self.assertEqual(Fraction(out["duration"]),
                         Fraction(2, 30))


class RateAndBufferTests(unittest.TestCase):
    def test_bit_rate_and_vbv_extensions(self):
        # bit_rate = (brext << 18 | value) * 400
        # vbv       = (vbvext << 10 | vbv) * 16384
        header = seq(352, 240, 2, 5, 1000, vbv=200) + \
            seq_ext(brext=7, vbvext=3)
        data = mpeg2(header, [("I", {})])
        out = summary(data)
        self.assertEqual(out["bit_rate"], ((7 << 18) | 1000) * 400)
        self.assertEqual(out["vbv_buffer_size"],
                         ((3 << 10) | 200) * 16 * 1024)

    def test_variable_bit_rate_is_null(self):
        data = mpeg2(seq(352, 240, 2, 5, 0x3FFFF) + seq_ext(),
                     [("I", {})])
        out = summary(data)
        self.assertIsNone(out["bit_rate"])

    def test_size_extensions(self):
        # 14-bit coded size: base 12 bits plus 2 extension bits.
        header = seq(0xABC, 0x123, 2, 5, 100) + seq_ext(hext=2, vext=1)
        data = mpeg2(header, [("I", {})])
        out = summary(data)
        self.assertEqual(out["width"], (2 << 12) | 0xABC)
        self.assertEqual(out["height"], (1 << 12) | 0x123)


class ProfileChromaTests(unittest.TestCase):
    def test_chroma_422_is_reported(self):
        data = mpeg2(seq(720, 480, 3, 4, 8000) + seq_ext(chroma=2),
                     [("I", {})])
        out = summary(data)
        self.assertEqual(out["chroma_format"], "4:2:2")

    def test_chroma_444(self):
        data = mpeg2(seq(352, 240, 2, 5, 100) + seq_ext(chroma=3),
                     [("I", {})])
        out = summary(data)
        self.assertEqual(out["chroma_format"], "4:4:4")

    def test_escape_profile_level_is_null(self):
        # profile_and_level_indication 0x85 (news_ntsc): escape bits, no
        # standard profile/level name.
        data = mpeg2(seq(720, 480, 3, 4, 8000) + seq_ext(0x85),
                     [("I", {})])
        out = summary(data)
        self.assertIsNone(out["profile"])
        self.assertIsNone(out["level"])

    def test_profile_422_name(self):
        # 13818-2 Amd.3: 0xA5 = 4:2:2 profile, level nibble 5 reserved.
        data = mpeg2(seq(720, 480, 3, 4, 8000) +
                     seq_ext(0xA5, chroma=2), [("I", {})])
        out = summary(data)
        self.assertEqual(out["profile"], "4:2:2")
        self.assertIsNone(out["level"])


class Mpeg1Tests(unittest.TestCase):
    def _header(self, aspect=1, frc=1, br=500, w=352, h=240):
        return seq(w, h, aspect, frc, br)

    def test_standard_detection(self):
        data = mpeg1(self._header(), ["I", "P", "B"])
        out = summary(data)
        self.assertEqual(out["standard"], "MPEG-1")
        self.assertIsNone(out["profile"])
        self.assertIsNone(out["level"])
        self.assertEqual(out["chroma_format"], "4:2:0")
        self.assertTrue(out["progressive_sequence"])

    def test_mpeg1_rates_and_duration(self):
        data = mpeg1(self._header(frc=4), ["I", "P", "B", "P"])
        out = summary(data)
        self.assertEqual(out["frame_rate"], "30000/1001")
        self.assertEqual(out["frames"], 4)
        self.assertEqual(sum(out["pictures"].values()), 4)
        self.assertEqual(Fraction(out["duration"]),
                         4 / Fraction(30000, 1001))

    def test_mpeg1_sample_aspect_ratio_code(self):
        # 11172-2 code 10 means SAR 1.0255 = 2051/2000.
        data = mpeg1(self._header(aspect=10, w=352, h=240),
                     ["I", "P"])
        out = summary(data)
        self.assertEqual(out["sar"], "2051:2000")
        expected_dar = Fraction(2051, 2000) * Fraction(352, 240)
        self.assertEqual(out["dar"], "%d:%d" % (
            expected_dar.numerator, expected_dar.denominator))

    def test_mpeg1_d_picture_type(self):
        data = mpeg1(self._header(), ["D", "D"])
        out = summary(data)
        self.assertEqual(out["pictures"]["D"], 2)
        self.assertEqual(out["frames"], 2)

    def test_mpeg1_quant_matrices_still_parse(self):
        data = mpeg1(seq(352, 240, 1, 1, 500, intra=True),
                     ["I", "P"])
        out = summary(data)
        self.assertEqual(out["standard"], "MPEG-1")
        self.assertEqual(out["frames"], 2)
        self.assertEqual(out["vbv_buffer_size"], 112 * 16 * 1024)


if __name__ == "__main__":
    unittest.main()
