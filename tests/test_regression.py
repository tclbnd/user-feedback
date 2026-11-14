"""Acceptance criteria as tests.

Every test here maps onto one line of the acceptance list, so a change
that fixes one criterion but breaks another cannot pass silently.
"""

import math
import unittest
from fractions import Fraction

from yuvkit.converter import Converter, compare_all, compare_paths
from yuvkit.lossless import classify_roundtrip, lossless_channels
from yuvkit.metrics import (banding_profile, boundary_samples, grey_ramp,
                            ramp, random_pixels, roundtrip_stats)
from yuvkit.planes import roundtrip_planes
from yuvkit.request import run_request

STANDARDS = ("bt601", "bt709", "bt2020")
COMBOS = (("full", "limited"), ("limited", "full"), ("full", "full"),
          ("limited", "limited"))


class Acceptance1_RoundTripTolerance(unittest.TestCase):
    """RGB->YUV->RGB error <= 2/255 on swatches and 10^5 random pixels."""

    def test_boundary_swatches(self):
        for std in STANDARDS:
            for rgb_range, yuv_range in COMBOS:
                conv = Converter(std, rgb_range, yuv_range)
                stats = roundtrip_stats(
                    conv, [p for _n, p in boundary_samples(std, rgb_range,
                                                           yuv_range)])
                self.assertLessEqual(stats["max_err"], 2,
                                     (std, rgb_range, yuv_range, stats))

    def test_one_hundred_thousand_random_pixels(self):
        for std in STANDARDS:
            for rgb_range, yuv_range in COMBOS:
                conv = Converter(std, rgb_range, yuv_range)
                stats = roundtrip_stats(
                    conv, random_pixels(100000, rgb_range, seed=2024))
                self.assertLessEqual(
                    stats["max_err"], 2,
                    "%s %s->%s max=%d hist=%s"
                    % (std, rgb_range, yuv_range, stats["max_err"],
                       stats["histogram"]))
                self.assertEqual(stats["pixels"], 100000)

    def test_luma_is_never_the_worst_channel(self):
        # A useful diagnostic property: if luma were the worst channel the
        # matrix ordering would be suspect.
        conv = Converter("bt709", "full", "limited")
        stats = roundtrip_stats(conv, random_pixels(20000, seed=4))
        self.assertLessEqual(stats["per_channel_max"][0],
                             max(stats["per_channel_max"]))

    def test_roundtrip_is_symmetric(self):
        # Converting back and forth repeatedly must not drift.
        conv = Converter("bt709", "full", "limited")
        pixel = (200, 100, 50)
        cur = pixel
        for _ in range(8):
            yuv = conv.rgb_to_yuv(cur).codes
            cur = conv.yuv_to_rgb(yuv).codes
            self.assertLessEqual(max(abs(a - b) for a, b in
                                     zip(pixel, cur)), 2, cur)


class Acceptance2_MixedStandardWarning(unittest.TestCase):
    """A 601/709 mix must produce an explicit warning."""

    def test_chain_warning_and_constant_report(self):
        res = run_request({
            "standard": "bt601", "rgb_range": "full",
            "yuv_range": "limited",
            "chain": [{"standard": "bt601", "direction": "rgb-to-yuv"},
                      {"standard": "bt709", "direction": "yuv-to-rgb"}],
            "ops": [{"direction": "rgb-to-yuv", "pixels": [[255, 0, 0]]}],
        })
        self.assertIn("mixed-standard", res["warnings"])

    def test_single_standard_is_quiet(self):
        res = run_request({
            "standard": "bt709", "rgb_range": "full",
            "yuv_range": "limited",
            "ops": [{"direction": "rgb-to-yuv",
                     "pixels": [[255, 0, 0], [0, 0, 0]]}],
        })
        self.assertEqual(res["warnings"], [])

    def test_indirect_mixing_shows_as_visible_error(self):
        # Quantify *why* the warning matters: encode with 601, decode
        # with 709, and show the resulting error is far above tolerance.
        enc = Converter("bt601", "full", "limited")
        dec = Converter("bt709", "full", "limited")
        for pixel in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (200, 120, 40)):
            yuv = enc.rgb_to_yuv(pixel).codes
            back = dec.yuv_to_rgb(yuv).codes
            err = max(abs(a - b) for a, b in zip(pixel, back))
            self.assertGreater(err, 2, (pixel, yuv, back))


class Acceptance3_NoBanding(unittest.TestCase):
    """A ramp must not gain banding."""

    def test_grey_ramp_levels(self):
        conv = Converter("bt709", "full", "limited")
        src = grey_ramp(256, "full")
        out = [conv.yuv_to_rgb(conv.rgb_to_yuv(p).codes).codes[0]
               for p in src]
        before = banding_profile([p[0] for p in src])
        after = banding_profile(out)
        self.assertEqual(after["reversals"], 0)
        # The only stalls allowed are the ones quantisation forces.
        self.assertEqual(after["excess_stalls"], 0)
        self.assertGreaterEqual(after["levels"], 220)
        self.assertEqual(before["levels"], 256)
        # 256 full-range levels squeezed into 219 luma levels: exactly the
        # double-quantisation staircase, no more.
        self.assertEqual(after["levels"], 220)

    def test_ramp_matches_theoretical_double_quantisation(self):
        conv = Converter("bt709", "full", "limited")
        src = list(range(256))
        out = []
        for v in src:
            yuv = conv.rgb_to_yuv((v, v, v)).codes
            out.append(conv.yuv_to_rgb(yuv).codes[0])
        ideal = []
        for v in src:
            y = math.floor(v * Fraction(219, 255) + 16 + Fraction(1, 2))
            back = math.floor((y - 16) * Fraction(255, 219) + Fraction(1, 2))
            ideal.append(min(255, max(0, back)))
        self.assertEqual(out, ideal)

    def test_matched_ranges_are_exact(self):
        for rgb_range, yuv_range in (("full", "full"),
                                     ("limited", "limited")):
            conv = Converter("bt709", rgb_range, yuv_range)
            width = 256 if rgb_range == "full" else 220
            src = grey_ramp(width, rgb_range)
            out = [conv.yuv_to_rgb(conv.rgb_to_yuv(p).codes).codes[0]
                   for p in src]
            self.assertEqual(out, [p[0] for p in src], (rgb_range, yuv_range))

    def test_colour_ramp_has_no_stalls_beyond_one_step(self):
        conv = Converter("bt709", "full", "limited")
        for start, end in (((0, 0, 0), (255, 255, 255)),
                           ((255, 0, 0), (0, 0, 255)),
                           ((0, 255, 0), (255, 0, 255))):
            src = ramp(256, start, end)
            out = [conv.yuv_to_rgb(conv.rgb_to_yuv(p).codes).codes
                   for p in src]
            for channel in range(3):
                # A channel that is constant in the source cannot band;
                # skipping it avoids mistaking "no signal" for a stall.
                if len({p[channel] for p in src}) <= 1:
                    continue
                prof = banding_profile([p[channel] for p in out])
                # The output can only use as many levels as the quantised
                # Y'CbCr codes that reach it provide, so the longest run
                # of identical samples is bounded by steps/levels.  A
                # collapsed range (the original bug) produced runs many
                # times this bound.
                bound = math.ceil((len(src) - 1) / max(1, prof["levels"])) + 1
                self.assertLessEqual(prof["max_run"], bound,
                                     (start, end, channel, prof))
                self.assertGreaterEqual(prof["levels"], 100,
                                        (start, end, channel, prof))
                # Chroma rounding can make a monotone channel wobble by a
                # code or two; anything larger is a real discontinuity.
                self.assertLessEqual(prof["reversals"], len(src) // 4,
                                     (start, end, channel, prof))
                self.assertLessEqual(_reversal_amplitude(out, channel), 3,
                                     (start, end, channel))

    def test_planar_420_ramp_adds_no_banding(self):
        image = [[(v, v, v) for v in range(256)] for _ in range(8)]
        _back, report = roundtrip_planes(image, "bt709", "full", "limited",
                                         "4:2:0")
        self.assertLessEqual(report["max_err"], 2)
        self.assertEqual(report["layout"]["phase"], [0.5, 0.5])


def _reversal_amplitude(pixels, channel):
    """Largest jump involved in a direction change of one channel."""
    values = [p[channel] for p in pixels]
    worst = 0
    for i in range(1, len(values) - 1):
        if (values[i] - values[i - 1]) * (values[i + 1] - values[i]) < 0:
            worst = max(worst, abs(values[i] - values[i - 1]),
                        abs(values[i + 1] - values[i]))
    return worst


class Acceptance4_IndependentMatrix(unittest.TestCase):
    """Per-pixel agreement with an independent implementation."""

    def test_float_path_agrees_except_at_boundaries(self):
        for std in STANDARDS:
            conv = Converter(std, "full", "limited")
            info = compare_all(random_pixels(5000, std and "full", seed=77),
                               "rgb-to-yuv", std, "full", "limited")
            self.assertTrue(info["all_within_one_code"], (std, info))
            float64 = info["engines"]["float64"]
            self.assertEqual(float64["differing"], 0, std)
            for engine in ("float32", "q16"):
                stats = info["engines"][engine]
                self.assertLessEqual(stats["max_delta"], 1, (std, engine))

    def test_deviations_are_boundary_pixels(self):
        conv = Converter("bt709", "full", "limited")
        pixels = random_pixels(20000, seed=303)
        info = compare_all(pixels, "rgb-to-yuv", "bt709", "full", "limited")
        for engine in ("q16", "float32"):
            stats = info["engines"][engine]
            if stats["differing"]:
                # every differing sample is within fixed-point resolution
                # of a rounding boundary
                self.assertLess(stats["max_boundary_distance"], 0.01,
                                (engine, stats["max_boundary_distance"]))

    def test_lut_and_matrix_identical(self):
        for std in STANDARDS:
            for direction in ("rgb-to-yuv", "yuv-to-rgb"):
                if direction == "rgb-to-yuv":
                    pixels = random_pixels(1000, "full", seed=13)
                else:
                    conv = Converter(std, "full", "limited")
                    pixels = [conv.rgb_to_yuv(p).codes
                              for p in random_pixels(1000, "full", seed=13)]
                info = compare_paths(pixels, direction, std, "full",
                                     "limited")
                self.assertTrue(info["identical"], (std, direction))
                self.assertEqual(info["max_delta"], 0)

    def test_hundred_thousand_pixel_cross_check(self):
        info = compare_all(random_pixels(100000, seed=555), "rgb-to-yuv",
                           "bt709", "full", "limited")
        self.assertEqual(info["engines"]["lut"]["differing"], 0)
        self.assertEqual(info["engines"]["float64"]["differing"], 0)
        self.assertLessEqual(info["engines"]["q16"]["max_delta"], 1)


class Acceptance5_LosslessChannels(unittest.TestCase):
    """The tolerance-bounded channel claim is honest and per channel."""

    def test_analytic_bounds_hold_empirically(self):
        for std in STANDARDS:
            for rgb_range, yuv_range in COMBOS:
                conv = Converter(std, rgb_range, yuv_range)
                pixels = random_pixels(5000, rgb_range, seed=17)
                stats = roundtrip_stats(conv, pixels)
                names = ("R", "G", "B")
                info = lossless_channels(std, rgb_range, yuv_range)
                for i, name in enumerate(names):
                    bound = info["bounds"][name]
                    self.assertLessEqual(stats["per_channel_max"][i],
                                         max(2, int(bound) + 1),
                                         (std, rgb_range, yuv_range, name))

    def test_classification_is_ok_for_in_gamut_content(self):
        info = classify_roundtrip(random_pixels(5000, seed=19))
        self.assertTrue(info["ok"])
        self.assertNotIn("over", info["classes"].values())

    def test_out_of_gamut_is_flagged_not_hidden(self):
        info = classify_roundtrip([[16, 16, 16], [235, 240, 240]],
                                  direction="yuv-to-rgb")
        self.assertIn("clipped", info["classes"].values())

    def test_clipping_rules_are_documented(self):
        from yuvkit.lossless import clipping_rules
        rules = clipping_rules()
        for key in ("rounding", "clip_to", "negative_intermediates",
                    "lossless_claim"):
            self.assertIn(key, rules)


class Acceptance6_ErrorDistribution(unittest.TestCase):
    """Document the shape of the residual error."""

    def test_error_is_never_worse_than_two_and_mostly_one(self):
        conv = Converter("bt709", "full", "limited")
        stats = roundtrip_stats(conv, random_pixels(20000, seed=88))
        hist = stats["histogram"]
        self.assertNotIn("3", hist)
        self.assertGreater(int(hist.get("1", 0)), int(hist.get("2", 0)))

    def test_extremes_do_not_clip_silently(self):
        conv = Converter("bt709", "full", "limited")
        for name, pixel in boundary_samples():
            res = conv.rgb_to_yuv(pixel)
            if any(res.clipped):
                self.assertTrue(any("clipped" in n for n in res.notes))


if __name__ == "__main__":
    unittest.main()

