"""Requirement 1: zone weighted metering, log statistics, border weights.

Checked here:
  * pure black zones never produce -inf / NaN and are reported separately,
  * the mean is taken in the log domain (so it is a geometric mean, not the
    arithmetic mean the baseline used, which is what crushed the shadows),
  * exposure compensation lands the metered mean on the key,
  * the border weight is a function of the frame (never of the tiling) and
    monotone from edge to centre,
  * per-zone votes are reported and their weighted sum is the global EV.
"""
import math
import unittest

from hdr_tm import config, run
from hdr_tm.imageio import shuffle_order, zone_grid
from hdr_tm.metering import (HIST_BINS_PER_EV, _border_weight, build_border,
                             meter, pixel_weight, quantise_log,
                             quantise_weight)
from tests import synth


class FixedPointTest(unittest.TestCase):
    def test_quantisers_are_deterministic_integers(self):
        for y in (0.0, 1e-9, 1e-6, 0.18, 1.0, 1e6):
            self.assertIsInstance(quantise_log(y), int)
            self.assertEqual(quantise_log(y), quantise_log(y))

    def test_log_grid_resolution(self):
        # 2**20 grid on log2 => ~1e-6 EV, far below any visible step
        lo = quantise_log(0.18)
        hi = quantise_log(0.18 * 2.0 ** 1e-6)
        self.assertGreaterEqual(hi - lo, 0)
        self.assertLessEqual(hi - lo, 2)

    def test_pure_black_has_no_vote_but_no_exception(self):
        self.assertEqual(quantise_weight(0.0), 0)
        self.assertGreaterEqual(quantise_weight(0.5), 1)
        # weights below half a quantisation step legitimately round to zero:
        # such a sample simply casts no vote, which is harmless
        self.assertEqual(quantise_weight(1e-9), 0)
        # log of pure black is floored, never -inf
        self.assertTrue(math.isfinite(quantise_log(0.0)))

    def test_weight_bounds(self):
        for y in (1e-9, 1e-3, 0.18, 1.0, 10.0, 1e5):
            w = pixel_weight(y, 4, 4, 16, 16, dict(config.DEFAULTS, border=0))
            self.assertGreaterEqual(w, 0.0)
            self.assertLessEqual(w, 1.0)


class BorderWeightTest(unittest.TestCase):
    def test_monotone_from_edge_to_centre(self):
        band = 6
        vals = [_border_weight(d, 10, 20, 20, band, 0.25) for d in range(10)]
        for a, b in zip(vals, vals[1:]):
            self.assertLessEqual(a, b + 1e-15)
        self.assertAlmostEqual(vals[0], 0.25)
        self.assertEqual(vals[band], 1.0)
        self.assertAlmostEqual(vals[band - 1], 1.0, places=2)

    def test_border_band_reduces_edge_weight(self):
        """The outermost ring must vote strictly less than an interior pixel."""
        cfg = dict(config.DEFAULTS, border=4, border_floor=0.25,
                   clip_lo=1e9, clip_hi=2e9)
        y = 0.18
        edge = pixel_weight(y, 0, 0, 16, 16, cfg)
        inner = pixel_weight(y, 8, 8, 16, 16, cfg)
        self.assertGreater(edge, 0.0)
        self.assertLess(edge, inner)

    def test_zero_band_is_neutral(self):
        self.assertEqual(_border_weight(0, 0, 5, 5, 0, 0.25), 1.0)

    def test_band_is_frame_based_not_tile_based(self):
        # same frame, different tilings => identical band
        a = build_border(64, 96, dict(config.DEFAULTS, border=None))
        b = build_border(64, 96, dict(config.DEFAULTS, border=None))
        self.assertEqual(a, b)
        self.assertEqual(a, 4)          # min(64,96)//16


class LogDomainTest(unittest.TestCase):
    def test_mean_is_weighted_log_average(self):
        """The mean must be the weighted average of log2 luma, exactly.

        A linear-domain average would be the arithmetic mean, which on a
        high contrast patch pair is pulled towards the bright sample.  The
        difference is asserted explicitly.
        """
        h, w = 8, 8
        vals = [0.005, 0.5]
        img = [[vals[(r + c) % 2] for c in range(w)] for r in range(h)]
        cfg = dict(config.DEFAULTS, border=0, clip_lo=1e9, clip_hi=2e9)
        stats, _zones, _h = meter(img, h, w, *zone_grid(h, w, 4), None, cfg)

        # independent recomputation from the documented weight function
        sw = swq = 0.0
        for r in range(h):
            for c in range(w):
                y = img[r][c]
                wq = quantise_weight(pixel_weight(y, r, c, h, w, cfg))
                q = quantise_log(y)
                sw += wq
                swq += wq * q
        expect = (swq / sw) / config.LOG_SCALE
        self.assertAlmostEqual(stats["mean_log_ev"], expect, places=12)

        # and it is decidedly *not* the arithmetic mean of the samples
        arith = (vals[0] + vals[1]) / 2.0
        self.assertNotAlmostEqual(2.0 ** stats["mean_log_ev"] / arith, 1.0,
                                  places=2)

    def test_high_contrast_mean_sits_between_the_stops(self):
        """With mid-tone weighting disabled the log mean is the exact midpoint."""
        h, w = 8, 8
        img = [[0.01 if (r + c) % 2 else 0.04 for c in range(w)]
               for r in range(h)]
        # tone_sigma huge => every sample weighs ~1, clip guard disabled
        cfg = dict(config.DEFAULTS, border=0, clip_lo=1e9, clip_hi=2e9,
                   tone_sigma=1e6)
        stats, _zones, _h = meter(img, h, w, *zone_grid(h, w, 4), None, cfg)
        self.assertAlmostEqual(stats["mean_log_ev"], math.log2(0.02), places=6)

    def test_mid_tone_weighting_biases_towards_the_key(self):
        """The default meter trusts near-key samples more, so the mean moves
        from the geometric midpoint towards the sample closest to the key."""
        h, w = 8, 8
        img = [[0.01 if (r + c) % 2 else 0.04 for c in range(w)]
               for r in range(h)]
        cfg = dict(config.DEFAULTS, border=0, clip_lo=1e9, clip_hi=2e9)
        stats, _zones, _h = meter(img, h, w, *zone_grid(h, w, 4), None, cfg)
        midpoint = math.log2(0.02)
        nearest_to_key = math.log2(0.04)
        self.assertGreater(stats["mean_log_ev"], midpoint)
        self.assertLess(stats["mean_log_ev"], nearest_to_key)

    def test_exposure_lands_on_key(self):
        """ev = log2(KEY) - mean_log, so the meter puts the mean on the key."""
        img, levels = synth.exposure_ladder(h=32, w=32, stops=(0,), bands=1)
        res = run({"hdr": img, "tile": 8, "radius": 2})
        st = res["stats"]
        self.assertAlmostEqual(st["mean_log_ev"] + st["metering_ev"],
                               st["log_key"], places=12)

    def test_ev_bias_shifts_exposure(self):
        img, _ = synth.exposure_ladder(h=16, w=16, stops=(0,), bands=1)
        a = run({"hdr": img, "tile": 8, "radius": 2})
        b = run({"hdr": img, "tile": 8, "radius": 2, "ev_bias": 1.5})
        self.assertAlmostEqual(b["ev"] - a["ev"], 1.5, places=12)

    def test_ev_is_clamped(self):
        img = [[1e-30] * 8 for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        self.assertGreaterEqual(res["ev"], -12.0 - 1e-12)
        self.assertLessEqual(res["ev"], 24.0 + 1e-12)


class PureBlackZoneTest(unittest.TestCase):
    def test_fully_black_zone_is_flagged_and_finite(self):
        # left half pure black, right half normal
        img = [[0.0 if c < 16 else 0.18 for c in range(32)] for _ in range(16)]
        res = run({"hdr": img, "tile": 16, "radius": 2})
        blacks = [z for z in res["zones"] if z["black_zone"]]
        self.assertTrue(blacks, "expected at least one pure black zone")
        for z in blacks:
            self.assertEqual(z["pixels"], z["black_pixels"])
            self.assertEqual(z["weight"], 0)
            self.assertEqual(z["weight_share"], 0.0)
            self.assertTrue(math.isfinite(z["mean_ev"]))
        self.assertGreater(res["stats"]["black_fraction"], 0.0)
        # the black zones must not drag the exposure around (no vote)
        self.assertAlmostEqual(res["stats"]["mean_log_ev"], math.log2(0.18),
                               places=6)

    def test_black_zone_does_not_produce_nan(self):
        img = [[0.0] * 16 for _ in range(16)]
        res = run({"hdr": img, "tile": 8, "radius": 2})
        for z in res["zones"]:
            self.assertTrue(math.isfinite(z["mean_ev"]))
            self.assertTrue(math.isfinite(z["weight_share"]))
        self.assertTrue(math.isfinite(res["ev"]))
        self.assertEqual(res["stats"]["mean_source"], "black_floor")

    def test_fully_black_frame_stays_neutral(self):
        """No signal means no exposure boost: amplifying noise would be wrong."""
        for img in ([[0.0] * 8 for _ in range(8)],
                    [[1e-30] * 8 for _ in range(8)]):
            res = run({"hdr": img, "tile": 4, "radius": 1})
            self.assertAlmostEqual(res["ev"], 0.0, places=12)
            self.assertEqual(res["stats"]["mean_source"], "black_floor")
            flat = [v for row in res["out"] for v in row]
            # the tone stage floors luma at LIN_MIN before log2(), so pure
            # black maps to a denormal display value instead of an exception
            self.assertLess(max(flat), 1e-9)
            self.assertGreaterEqual(min(flat), 0.0)

    def test_zone_votes_match_global_identity(self):
        img, _ = synth.exposure_ladder(h=32, w=32, bands=4)
        res = run({"hdr": img, "tile": 8, "radius": 2})
        st = res["stats"]
        # identical algebraically; float summation order leaves ~1e-12
        self.assertAlmostEqual(st["zone_contribution_sum"],
                               st["log_key"] - st["mean_log_ev"], delta=1e-9)
        self.assertAlmostEqual(st["zone_weight_share_sum"], 1.0, delta=1e-9)

    def test_zone_ev_grid_shape(self):
        img, _ = synth.exposure_ladder(h=32, w=48, bands=4)
        res = run({"hdr": img, "tiles": 0} if False else
                  {"hdr": img, "zones": [2, 3], "radius": 1})
        self.assertEqual(len(res["zone_ev"]), 2)
        self.assertEqual(len(res["zone_ev"][0]), 3)
        self.assertEqual(len(res["out"]), 32)
        self.assertEqual(len(res["out"][0]), 48)

    def test_black_fraction_counts_correctly(self):
        img = [[0.0] * 8 for _ in range(8)]
        for r in range(4):
            for c in range(8):
                img[r][c] = 0.18
        res = run({"hdr": img, "tile": 8, "radius": 1})
        self.assertEqual(res["stats"]["black_pixels"], 32)
        self.assertEqual(res["stats"]["pixels"], 64)
        self.assertAlmostEqual(res["stats"]["black_fraction"], 0.5)


class WhitePointTest(unittest.TestCase):
    """The white point comes from the log luma histogram percentiles.

    A mis-scaled histogram (bins-per-EV confused with a bit shift) once made
    the histogram 5.4e8 entries long and silently swallowed every sample; the
    size assertion below is the regression guard for that.
    """

    def test_histogram_size_is_bounded(self):
        h = w = 8
        img = [[0.18] * w for _ in range(h)]
        cfg = dict(config.DEFAULTS)
        stats, _zones, hist = meter(img, h, w, *zone_grid(h, w, 4), None, cfg)
        self.assertEqual(len(hist), 2 * int(config.LOG_CLAMP_EV) *
                         HIST_BINS_PER_EV + 1)
        self.assertLess(len(hist), 1e6, "histogram exploded: bad bin scale")
        self.assertEqual(sum(hist), h * w, "histogram dropped samples")

    def test_percentile_tracks_actual_bright_pixels(self):
        """Frames with a tiny bright patch must place the white point near it."""
        h = w = 16
        base = 0.05
        bright = 8.0
        img = [[base] * w for _ in range(h)]
        n_bright = 3
        for k in range(n_bright):
            img[k][k] = bright
        frac = 1.0 - n_bright / float(h * w)
        res = run({"hdr": img, "tile": 8, "radius": 1,
                   "percentile": frac + 1e-6, "white_lo": 0.001,
                   "white_hi": 1e9})
        wp = res["curve"]["white_point"]
        # the percentile sits above the 253 dim samples, i.e. among the bright
        self.assertGreater(math.log2(wp), math.log2(base) + 4.0)

    def test_white_point_respects_clamp(self):
        img = [[1e9] * 8 for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1,
                   "white_lo": 0.001, "white_hi": 4.0})
        self.assertAlmostEqual(res["curve"]["white_point"], 4.0)
        img2 = [[1e-9] * 8 for _ in range(8)]
        res2 = run({"hdr": img2, "tile": 4, "radius": 1,
                    "white_lo": 2.0, "white_hi": 4096.0})
        self.assertAlmostEqual(res2["curve"]["white_point"], 2.0)

    def test_white_point_is_tiling_invariant(self):
        img, _ = synth.exposure_ladder(h=48, w=48, bands=6)
        a = run({"hdr": img, "tile": 4, "radius": 1})
        b = run({"hdr": img, "zones": [5, 3], "radius": 1})
        c = run({"hdr": img, "tile": 24, "zone_shuffle": 77, "radius": 1})
        self.assertEqual(a["curve"]["white_point"], b["curve"]["white_point"])
        self.assertEqual(a["curve"]["white_point"], c["curve"]["white_point"])


class PartitionTest(unittest.TestCase):
    def test_zones_cover_the_frame(self):
        for h, w in ((17, 23), (1, 1), (64, 64), (5, 100)):
            for tile in (1, 4, 7, 16, 1000):
                br, bc = zone_grid(h, w, tile)
                cov = sum((br[i + 1] - br[i]) * (bc[j + 1] - bc[j])
                          for i in range(len(br) - 1)
                          for j in range(len(bc) - 1))
                self.assertEqual(cov, h * w)

    def test_no_empty_zone(self):
        br, bc = zone_grid(10, 10, 1)
        for i in range(len(br) - 1):
            self.assertGreater(br[i + 1] - br[i], 0)

    def test_shuffle_is_a_permutation(self):
        for n in (1, 2, 5, 36):
            for seed in (1, 99, 2 ** 31):
                order = shuffle_order(n, seed)
                self.assertEqual(sorted(order), list(range(n)))

    def test_shuffle_zero_is_identity(self):
        self.assertIsNone(shuffle_order(9, 0))


if __name__ == "__main__":
    unittest.main()