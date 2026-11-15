"""Acceptance 2: tiling and zone-order invariance.

The same HDR input must yield the same exposure parameters *and* the same
output digest under:
  * two different tile sizes,
  * an explicit zone count,
  * a permuted zone visit order with the same tiling.

Integer accumulation is what makes this exact rather than approximate, so the
comparison is `==` on the curve dict and on the digest, and a bitwise compare
on the output values.
"""
import unittest

from hdr_tm import run
from tests import synth


def _digest_row(values):
    return [v for v in values]


class InvarianceTest(unittest.TestCase):
    IMG = None
    LEVELS = None

    @classmethod
    def setUpClass(cls):
        cls.IMG, cls.LEVELS = synth.exposure_ladder(h=96, w=96, bands=8)

    def _run(self, **kw):
        req = {"hdr": self.IMG, "radius": 2}
        req.update(kw)
        return run(req)

    def test_tile_size_invariance(self):
        a = self._run(tile=4)
        b = self._run(tile=16)
        c = self._run(tile=32)
        self.assertEqual(a["ev"], b["ev"])
        self.assertEqual(a["ev"], c["ev"])
        self.assertEqual(a["curve"], b["curve"])
        self.assertEqual(a["curve"], c["curve"])
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["digest"], c["digest"])
        self.assertEqual(a["out"], b["out"])

    def test_zone_count_invariance(self):
        a = self._run(tile=8)
        b = self._run(zones=[3, 7])
        self.assertEqual(a["ev"], b["ev"])
        self.assertEqual(a["curve"], b["curve"])
        self.assertEqual(a["digest"], b["digest"])

    def test_zone_order_invariance(self):
        base = self._run(tile=8)
        for seed in (1, 7, 2024, 99991):
            shuffled = self._run(tile=8, zone_shuffle=seed)
            self.assertEqual(base["ev"], shuffled["ev"])
            self.assertEqual(base["curve"], shuffled["curve"])
            self.assertEqual(base["digest"], shuffled["digest"])
            self.assertEqual(base["out"], shuffled["out"])

    def test_global_stats_are_integer_sums(self):
        """The invariance comes from integer accumulation, so check it."""
        a = self._run(tile=4)
        b = self._run(tile=16)
        for key in ("weight_sum", "weight_sum_q", "pixels", "black_pixels"):
            self.assertEqual(a["stats"][key], b["stats"][key], key)
        self.assertEqual(a["stats"]["mean_log_ev"], b["stats"]["mean_log_ev"])

    def test_zone_breakdown_is_partition_dependent_but_conserved(self):
        """Per-zone output legitimately differs; the *sum* must not.

        The shares and contributions are floats built from exact integer
        accumulators, so they agree to ~1e-12 (not necessarily bitwise: the
        partition groups the same pixels into different float ratios).
        """
        a = self._run(tile=4)
        b = self._run(tile=16)
        self.assertNotEqual(a["stats"]["zone_count"], b["stats"]["zone_count"])
        self.assertAlmostEqual(a["stats"]["zone_contribution_sum"],
                               b["stats"]["zone_contribution_sum"], places=12)
        self.assertAlmostEqual(a["stats"]["zone_weight_share_sum"], 1.0, places=12)
        self.assertAlmostEqual(b["stats"]["zone_weight_share_sum"], 1.0, places=12)
        # and the conserved quantity is exactly the global exposure identity
        for res in (a, b):
            self.assertAlmostEqual(
                res["stats"]["zone_contribution_sum"],
                res["curve"]["log_key"] - res["stats"]["mean_log_ev"],
                places=12)

    def test_perturbed_input_changes_the_digest(self):
        """Guard: invariance must not be a constant answer."""
        img = [row[:] for row in self.IMG]
        img[5][5] *= 1.05
        other = run({"hdr": img, "tile": 8, "radius": 2})
        base = self._run(tile=8)
        self.assertNotEqual(base["digest"], other["digest"])


class ColourRecombinationTest(unittest.TestCase):
    """Brightness and colour are separated; only brightness may change."""

    @staticmethod
    def _luma(px):
        return 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]

    def test_hue_and_ratio_are_preserved(self):
        """A colour that stays in gamut keeps its exact ratios."""
        img = [[[0.2, 0.05, 0.02] for _ in range(8)] for _ in range(8)]
        # a low white point keeps the output well inside [0, 1]
        res = run({"hdr": img, "tile": 4, "radius": 1, "white_lo": 0.001,
                   "ev_bias": -4.0})
        out = res["rgb"][0][0]
        self.assertLess(max(out), 1.0, "test colour must stay in gamut")
        src_ratio = [1.0, 0.25, 0.10]
        out_ratio = [c / out[0] for c in out]
        for a, b in zip(src_ratio, out_ratio):
            self.assertAlmostEqual(a, b, places=9)
        # and the luma is the tone mapped one
        self.assertAlmostEqual(self._luma(out), res["out"][0][0], places=9)

    def test_out_of_gamut_trades_saturation_for_luma(self):
        """When the tone mapped luma cannot fit at full saturation, luma wins.

        A 0.64-luma pure red does not exist inside [0,1]^3: something has to
        give.  The pipeline keeps the authoritative brightness and
        desaturates towards grey, rather than clipping a channel (which would
        shift the hue) or dimming the pixel (which would lose the exposure).
        """
        img = [[[0.2, 0.05, 0.02] for _ in range(8)] for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        out = res["rgb"][0][0]
        self.assertLessEqual(max(out), 1.0)
        self.assertGreaterEqual(min(out), 0.0)
        # brightness is preserved exactly
        self.assertAlmostEqual(self._luma(out), res["out"][0][0], places=9)
        # hue is still red dominant, just less saturated
        self.assertGreater(out[0], out[1])
        self.assertGreater(out[1], out[2])
        # and it did not clip the red channel on its own
        self.assertAlmostEqual(max(out), 1.0, places=9)

    def test_unsaturated_pixel_matches_tone_map_luma(self):
        """A neutral pixel must land exactly on the tone mapped luma."""
        img = [[[0.18, 0.18, 0.18] for _ in range(8)] for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        px = res["rgb"][0][0]
        self.assertAlmostEqual(self._luma(px), res["out"][0][0], places=9)

    def test_out_of_gamut_is_desaturated_not_hue_shifted(self):
        """Per channel clipping would move the hue; soft desaturation does not."""
        img = [[[0.2, 0.05, 0.02] for _ in range(8)] for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        out = res["rgb"][0][0]
        self.assertLessEqual(max(out), 1.0)
        self.assertGreaterEqual(min(out), 0.0)
        # naive clipping would leave 1.0 / 0.25 / 0.1 ; desaturation lifts
        # the small channels towards grey instead
        self.assertGreater(out[1], 0.25 * out[0])
        self.assertGreater(out[2], 0.10 * out[0])

    def test_no_negative_channels(self):
        img = [[[0.001, 0.002, 1.0] for _ in range(8)] for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        for row in res["rgb"]:
            for px in row:
                for v in px:
                    self.assertGreaterEqual(v, 0.0)
                    self.assertLessEqual(v, 1.0)

    def test_black_pixel_stays_black(self):
        img = [[[0.0, 0.0, 0.0] for _ in range(8)] for _ in range(8)]
        res = run({"hdr": img, "tile": 4, "radius": 1})
        self.assertEqual(res["rgb"][0][0], [0.0, 0.0, 0.0])

    def test_saturation_knob_changes_chroma_only(self):
        img = [[[0.3, 0.15, 0.06] for _ in range(8)] for _ in range(8)]
        a = run({"hdr": img, "tile": 4, "radius": 1})
        b = run({"hdr": img, "tile": 4, "radius": 1, "saturation": 0.5})
        pa, pb = a["rgb"][0][0], b["rgb"][0][0]
        # luminance is unchanged by the chroma knob
        self.assertAlmostEqual(self._luma(pa), self._luma(pb), places=9)
        # but the colour is pulled towards grey
        self.assertGreater(pb[1] / pb[0], pa[1] / pa[0])


class ColourInvarianceTest(unittest.TestCase):
    def test_rgb_invariance(self):
        img = [[[0.3 * (1 + c / 10.0) * (1 + r / 40.0) for c in range(3)]
                for _ in range(24)] for r in range(24)]
        a = run({"hdr": img, "tile": 4, "radius": 2})
        b = run({"hdr": img, "zones": [2, 5], "zone_shuffle": 4242, "radius": 2})
        self.assertEqual(a["ev"], b["ev"])
        self.assertEqual(a["curve"], b["curve"])
        self.assertEqual(a["digest"], b["digest"])
        self.assertEqual(a["rgb"], b["rgb"])


if __name__ == "__main__":
    unittest.main()