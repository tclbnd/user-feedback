"""Converter behaviour: ordering, clipping, provenance, path agreement."""

import unittest

from yuvkit.converter import (ConversionError, Converter, EPSILON,
                              compare_all, compare_paths)
from yuvkit.reference import FixedPointReference, ReferenceConverter
from yuvkit.metrics import boundary_samples, random_pixels


class TestConstruction(unittest.TestCase):
    def test_defaults(self):
        conv = Converter()
        self.assertEqual(conv.standard, "bt709")
        self.assertEqual(conv.rgb_range, "full")
        self.assertEqual(conv.yuv_range, "limited")

    def test_aliases(self):
        conv = Converter("rec601", "pc", "tv")
        self.assertEqual(conv.standard, "bt601")
        self.assertEqual(conv.rgb_range, "full")
        self.assertEqual(conv.yuv_range, "limited")

    def test_bad_path(self):
        with self.assertRaises(ConversionError):
            Converter(path="magic")

    def test_tolerance_constant(self):
        from fractions import Fraction
        self.assertEqual(EPSILON, Fraction(2, 255))


class TestRangeOrdering(unittest.TestCase):
    """The core defect: range scaling must not touch the pre-matrix value."""

    def test_full_to_limited_endpoints(self):
        conv = Converter("bt709", "full", "limited")
        self.assertEqual(conv.rgb_to_yuv((0, 0, 0)).codes, (16, 128, 128))
        self.assertEqual(conv.rgb_to_yuv((255, 255, 255)).codes,
                         (235, 128, 128))

    def test_black_stays_black_not_grey(self):
        # Delivering full-range black into a limited pipeline used to
        # raise Y to ~16 *and* scale chroma to around 152 (grey).
        for std in ("bt601", "bt709", "bt2020"):
            conv = Converter(std, "full", "limited")
            y, cb, cr = conv.rgb_to_yuv((0, 0, 0)).codes
            self.assertEqual((cb, cr), (128, 128), std)
            self.assertEqual(y, 16, std)

    def test_saturated_primaries_keep_their_chroma_extremes(self):
        conv = Converter("bt709", "full", "limited")
        self.assertEqual(conv.rgb_to_yuv((255, 0, 0)).codes[2], 240)
        self.assertEqual(conv.rgb_to_yuv((0, 0, 255)).codes[1], 240)
        self.assertEqual(conv.rgb_to_yuv((0, 255, 0)).codes[1], 42)
        self.assertEqual(conv.rgb_to_yuv((0, 255, 0)).codes[2], 26)

    def test_limited_rgb_endpoints_map_to_limited_yuv_endpoints(self):
        conv = Converter("bt709", "limited", "limited")
        self.assertEqual(conv.rgb_to_yuv((16, 16, 16)).codes, (16, 128, 128))
        self.assertEqual(conv.rgb_to_yuv((235, 235, 235)).codes,
                         (235, 128, 128))

    def test_yuv_to_rgb_endpoints(self):
        conv = Converter("bt709", "full", "limited")
        self.assertEqual(conv.yuv_to_rgb((16, 128, 128)).codes, (0, 0, 0))
        self.assertEqual(conv.yuv_to_rgb((235, 128, 128)).codes,
                         (255, 255, 255))


class TestValidation(unittest.TestCase):
    def test_out_of_range_inputs_rejected_not_clipped(self):
        conv = Converter("bt709", "limited", "limited")
        with self.assertRaises(ConversionError):
            conv.rgb_to_yuv((0, 0, 0))
        with self.assertRaises(ConversionError):
            conv.rgb_to_yuv((255, 255, 255))
        with self.assertRaises(ConversionError):
            conv.yuv_to_rgb((0, 128, 128))

    def test_wrong_arity(self):
        conv = Converter()
        with self.assertRaises(ConversionError):
            conv.rgb_to_yuv((1, 2))
        with self.assertRaises(ConversionError):
            conv.rgb_to_yuv((1, 2, 3, 4))

    def test_float_codes_rejected(self):
        conv = Converter()
        with self.assertRaises(ConversionError):
            conv.rgb_to_yuv((1.5, 2, 3))

    def test_integral_float_accepted(self):
        conv = Converter()
        self.assertEqual(conv.rgb_to_yuv((1.0, 2.0, 3.0)).codes,
                         conv.rgb_to_yuv((1, 2, 3)).codes)


class TestClippingRules(unittest.TestCase):
    def test_negative_channel_is_clipped_and_reported(self):
        conv = Converter("bt709", "limited", "limited")
        res = conv.yuv_to_rgb((16, 16, 16))
        self.assertEqual(res.codes[0], 16)
        self.assertTrue(any("negative-R" in n for n in res.notes),
                        res.notes)
        self.assertTrue(res.clipped[0])

    def test_high_clip_and_notes(self):
        conv = Converter("bt709", "limited", "limited")
        res = conv.yuv_to_rgb((235, 240, 240))
        self.assertEqual(res.codes[0], 235)
        self.assertIn("clipped-high-R", res.notes)

    def test_clean_pixels_have_no_notes(self):
        conv = Converter("bt709", "full", "limited")
        res = conv.rgb_to_yuv((128, 128, 128))
        self.assertEqual(res.notes, [])
        self.assertEqual(res.clipped, (False, False, False))

    def test_unrounded_value_retained(self):
        conv = Converter("bt709", "limited", "full")
        res = conv.yuv_to_rgb((16, 16, 16))
        self.assertLess(res.raw[0].value, 0)


class TestProvenance(unittest.TestCase):
    def test_tags_carry_ranges_and_standard(self):
        conv = Converter("bt601", "full", "limited", layout="4:2:0")
        res = conv.rgb_to_yuv((255, 0, 0))
        tags = res.tags.describe()
        self.assertEqual(tags["standard"], "bt601")
        self.assertEqual(tags["rgb_range"], "full")
        self.assertEqual(tags["yuv_range"], "limited")
        self.assertEqual(tags["layout"], "4:2:0")
        self.assertEqual(tags["chroma_phase"], [0.5, 0.5])

    def test_trace_record_is_json_ready(self):
        conv = Converter("bt709", "full", "limited")
        rec = conv.rgb_to_yuv((10, 20, 30)).trace(index=0,
                                                  source=(10, 20, 30))
        self.assertEqual(rec["src"], [10, 20, 30])
        self.assertEqual(rec["dst"],
                         list(conv.rgb_to_yuv((10, 20, 30)).codes))
        self.assertEqual(len(rec["raw"]), 3)
        self.assertEqual(rec["tags"]["path"], "matrix")


class TestRoundtrip(unittest.TestCase):
    def test_boundary_swatches(self):
        for std in ("bt601", "bt709", "bt2020"):
            conv = Converter(std, "full", "limited")
            for name, pixel in boundary_samples(std):
                _a, _b, err = conv.roundtrip_pixel(pixel, "rgb-to-yuv")
                self.assertLessEqual(err, 2, (std, name, pixel))
                self.assertLessEqual(err, 2, name)

    def test_random(self):
        conv = Converter("bt709", "full", "limited")
        for pixel in random_pixels(2000, seed=21):
            err = conv.roundtrip_pixel(pixel, "rgb-to-yuv")[2]
            self.assertLessEqual(err, 2)


class TestPaths(unittest.TestCase):
    def test_lut_and_matrix_identical(self):
        for std in ("bt601", "bt709", "bt2020"):
            pixels = random_pixels(300, seed=3)
            conv = Converter(std, "full", "limited")
            other = Converter(std, "full", "limited", path="lut")
            for pixel in pixels:
                self.assertEqual(conv.rgb_to_yuv(pixel).codes,
                                 other.rgb_to_yuv(pixel).codes)
                self.assertEqual(list(conv.rgb_to_yuv(pixel).codes),
                                 [r.code for r in
                                  other.rgb_to_yuv(pixel).raw])

    def test_compare_paths_reports_identity(self):
        info = compare_paths(random_pixels(50, seed=1))
        self.assertTrue(info["identical"])
        self.assertEqual(info["max_delta"], 0)

    def test_compare_all_engines(self):
        info = compare_all(random_pixels(500, seed=8))
        self.assertTrue(info["all_within_one_code"])
        self.assertEqual(info["engines"]["lut"]["differing"], 0)
        self.assertEqual(info["engines"]["float64"]["differing"], 0)


class TestReference(unittest.TestCase):
    def test_float_reference_matches_exact_path(self):
        conv = Converter("bt709", "full", "limited")
        ref = ReferenceConverter("bt709", "full", "limited")
        for pixel in random_pixels(500, seed=5):
            self.assertEqual(conv.rgb_to_yuv(pixel).codes,
                             ref.rgb_to_yuv(pixel))

    def test_float32_deviations_are_one_code_only(self):
        conv = Converter("bt709", "full", "limited")
        ref = ReferenceConverter("bt709", "full", "limited")
        for pixel in random_pixels(500, seed=5):
            delta = max(abs(a - b) for a, b in zip(
                conv.rgb_to_yuv(pixel).codes,
                ref.convert_f32(pixel, "rgb-to-yuv")))
            self.assertLessEqual(delta, 1)

    def test_fixed_point_deviations_are_one_code_only(self):
        conv = Converter("bt709", "full", "limited")
        ref = FixedPointReference("bt709", "full", "limited")
        for pixel in random_pixels(500, seed=6):
            delta = max(abs(a - b) for a, b in zip(
                conv.rgb_to_yuv(pixel).codes,
                ref.convert(pixel, "rgb-to-yuv")))
            self.assertLessEqual(delta, 1)


if __name__ == "__main__":
    unittest.main()

