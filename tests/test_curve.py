"""Acceptance 1: the tone curve reconstructs a synthetic exposure ladder.

The ladder is a set of flat patches at exact stops around middle grey.  Flat
patches have a zero detail layer, so the pipeline output must equal the
documented target model, and the per-stop absolute error must stay <= 0.01.

The second half checks the curve itself is monotone, bounded and asymptotic,
i.e. the highlight clipping of the baseline cannot come back.
"""
import math
import unittest

from hdr_tm import run, target_display
from hdr_tm import tone
from tests import synth


class ShadowGainCapTest(unittest.TestCase):
    """The dark-side gain must be a real, binding cap -- not decoration.

    lift_k * depth grows without bound as a patch gets darker, so deep
    shadows must sit exactly on the cap while mid shadows do not.
    """

    def setUp(self):
        cfg = {"pivot": 0.0, "lift_k": 2.0}
        self.cfg = cfg
        self.cap = math.log2(3.0)

    def test_deep_shadows_sit_on_the_cap(self):
        for e in (-12.0, -8.0, -4.0, -2.0):
            self.assertAlmostEqual(tone.shadow_lift(e, self.cfg, self.cap),
                                   self.cap, places=12)

    def test_shallow_shadows_are_not_capped(self):
        e = -0.5
        lift = tone.shadow_lift(e, self.cfg, self.cap)
        self.assertAlmostEqual(lift, 2.0 * 0.5, places=12)
        self.assertLess(lift, self.cap)

    def test_no_lift_at_or_above_pivot(self):
        for e in (0.0, 1.0, 5.0):
            self.assertEqual(tone.shadow_lift(e, self.cfg, self.cap), 0.0)

    def test_linear_gain_never_exceeds_three(self):
        for e in [x * 0.25 for x in range(-80, 5)]:
            lift = tone.shadow_lift(e, self.cfg, self.cap)
            self.assertLessEqual(2.0 ** lift, 3.0 + 1e-12)

    def test_cap_matches_config(self):
        res = run({"hdr": [[0.01] * 8 for _ in range(8)], "radius": 1,
                   "max_dark_gain": 2.5})
        self.assertAlmostEqual(res["curve"]["shadow_gain_cap_ev"],
                               math.log2(2.5), places=12)
        self.assertAlmostEqual(res["curve"]["shadow_gain_cap_linear"], 2.5)

    def test_lift_k_above_cap_is_rejected(self):
        with self.assertRaises(ValueError):
            run({"hdr": [[0.1]], "lift_k": 5.0, "max_dark_gain": 3.0})


class LadderTest(unittest.TestCase):
    STOPS = (-10, -6, -3, 0, 3, 6, 9, 12, 15, 18)
    TOL = 0.01

    def setUp(self):
        self.img, self.levels = synth.exposure_ladder(
            h=96, w=96, stops=self.STOPS)
        self.res = run({"hdr": self.img, "tile": 8, "radius": 2})
        self.h = len(self.img)
        self.w = len(self.img[0])

    def band_row(self, band):
        """A row that actually belongs to `band` (integer banding is uneven)."""
        rows = [r for r in range(self.h)
                if min(len(self.levels) - 1, r * len(self.levels) // self.h)
                == band]
        self.assertTrue(rows)
        return rows[len(rows) // 2]

    def test_the_ladder_really_is_a_ladder(self):
        """Guard: the synthetic input must contain every stop exactly."""
        seen = set()
        for r in range(self.h):
            seen.add(min(len(self.levels) - 1,
                         r * len(self.levels) // self.h))
        self.assertEqual(len(seen), len(self.levels))
        for band in range(len(self.levels)):
            r = self.band_row(band)
            self.assertAlmostEqual(self.img[r][0], self.levels[band],
                                   places=15)

    def test_each_stop_within_tolerance(self):
        worst = 0.0
        for band, y in enumerate(self.levels):
            r = self.band_row(band)
            got = self.res["out"][r][self.w // 2]
            tgt = target_display(y, self.res)
            err = abs(got - tgt)
            worst = max(worst, err)
            self.assertLessEqual(
                err, self.TOL,
                "band %d (%.4g) got %.6f target %.6f" % (band, y, got, tgt))
        # the measured error is at rounding level; keep the 0.01 contract but
        # also record how much headroom is actually left
        self.assertLessEqual(worst, 1e-9, "unexpected reconstruction drift")

    def test_patches_are_flat(self):
        # a flat patch must map to a single value: no detail leakage
        for band in range(len(self.STOPS)):
            r = self.band_row(band)
            row = self.res["out"][r]
            self.assertLessEqual(max(row) - min(row), 1e-12,
                                 "band %d is not flat" % band)

    def test_monotone_in_step(self):
        vals = [self.res["out"][self.band_row(b)][self.w // 2]
                for b in range(len(self.STOPS))]
        for a, b in zip(vals, vals[1:]):
            self.assertLess(a, b, "ladder is not increasing: %r" % (vals,))

    def test_highlights_do_not_clip(self):
        # +12 stops is 4096x middle grey; a hard clip would give exactly 1.0
        top = self.res["out"][self.band_row(len(self.STOPS) - 1)][self.w // 2]
        self.assertLess(top, 1.0)
        self.assertGreater(top, 0.99)

    def test_shadows_are_not_crushed(self):
        # -10 stops must stay strictly positive, not collapse to 0
        bottom = self.res["out"][self.band_row(0)][self.w // 2]
        self.assertGreater(bottom, 0.0)

    def test_dark_gain_cap_holds(self):
        """The shadow lift may not exceed max_dark_gain in linear terms."""
        cap = math.log2(self.res["curve"]["shadow_gain_cap_linear"])
        for y in (1e-4, 1e-3, 1e-2):
            e = math.log2(y) + self.res["ev"] - self.res["curve"]["log_key"]
            cfg = {"pivot": self.res["curve"]["pivot"],
                   "lift_k": self.res["curve"]["lift_k"]}
            lift = tone.shadow_lift(e, cfg, cap)
            self.assertLessEqual(lift, cap + 1e-12)
            self.assertGreaterEqual(lift, 0.0)


class CurveShapeTest(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(tone.target(0.0), 0.0)
        self.assertEqual(tone.target(tone.U_MAX), 1.0)

    def test_strictly_increasing(self):
        prev = -1.0
        for k in range(-40, 41):
            v = tone.target(2.0 ** (k * 0.25))
            self.assertGreater(v, prev)
            prev = v

    def test_bounded(self):
        # T(u) = 1 - (C + (B-A)u) / (u^2 + Bu + C) < 1 for every finite u.
        # Beyond u ~ 2**24 the deficit falls under double precision (C/u^2
        # < 1e-15), so the ladder is checked up to there; any realistic HDR
        # input (u = 2**e with |e| <= 24 EV) stays strictly below 1.
        for k in range(0, 25):
            v = tone.target(2.0 ** k)
            self.assertLess(v, 1.0)
        self.assertEqual(tone.target(tone.U_MAX), 1.0)

    def test_inverse_roundtrip(self):
        for y in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99):
            u = tone.curve_solve(y)
            self.assertAlmostEqual(tone.target(u), y, places=10)

    def test_pow2_saturates(self):
        self.assertEqual(tone.pow2(float("nan")), 1.0)
        self.assertEqual(tone.pow2(1e9), tone.U_MAX)
        self.assertEqual(tone.pow2(-1e9), tone.U_MIN)


if __name__ == "__main__":
    unittest.main()