"""Acceptance 2 (edge half): no haloing at a strong edge.

Two independent checks:

  1. *Kernel level*: the cross-edge mass of the guidance kernel must be many
     orders of magnitude below a plain box kernel's.  That mass is exactly
     what carries the bright side's energy into the shadows, so its absence
     is the mechanism behind the halo-free result.

  2. *Image level*: on a step edge the tone mapped output of each flat side
     must stay flat right up to the boundary, i.e. the dark side must not be
     lifted near the edge (halo) and the bright side must not be dragged down
     (dark rim).  Measured in display units against each side's far-field
     value.
"""
import math
import unittest

from hdr_tm import run
from hdr_tm.guided import cross_edge_weight_sum
from tests import synth


class HaloKernelTest(unittest.TestCase):
    def setUp(self):
        self.img, self.edge = synth.step_edge(h=48, w=48, lo=0.02, hi=1.6)
        self.h = len(self.img)
        self.w = len(self.img[0])
        self.L = [math.log2(v) for row in self.img for v in row]
        self.cfg = {"radius": 3, "sigma_r": 0.35, "rel_cut": 1e-6}

    def test_cross_edge_mass_is_negligible(self):
        cross, box_ref = cross_edge_weight_sum(
            self.L, self.h, self.w, self.cfg, self.edge, 3)
        self.assertGreater(box_ref, 1.0, "reference mass must be substantial")
        # the bilateral kernel must suppress the cross-edge term by > 1e-6
        self.assertLessEqual(cross / box_ref, 1e-6)

    def test_cross_edge_mass_grows_for_narrow_range_sigma(self):
        """Sanity: the suppression is caused by the range term, not by luck."""
        wide = dict(self.cfg, sigma_r=50.0)     # ~no range weighting
        cross_wide, box_ref = cross_edge_weight_sum(
            self.L, self.h, self.w, wide, self.edge, 3)
        cross_narrow, _ = cross_edge_weight_sum(
            self.L, self.h, self.w, self.cfg, self.edge, 3)
        self.assertGreater(cross_wide, cross_narrow * 1e6)


class GuidanceWeightCancellationTest(unittest.TestCase):
    """The literal acceptance statement: the guidance weights of an edge's two
    sides sum to zero.

    For a step edge the mirror pair (p, q) at equal distance from the edge has
    identical detail magnitude, so the guidance weights w = B - L satisfy
    w(p) + w(q) = 0 to floating point.  The test insists that this is a real
    cancellation (weights are non-negligible at moderate steps), not two
    zeros.
    """

    CFG = {"radius": 3, "sigma_r": 0.35, "rel_cut": 1e-6}

    def _mirror_residual(self, dL_ev):
        from hdr_tm.guided import bilateral_base
        h = w = 40
        edge = 20
        lo = math.log2(0.18)
        L = [lo + dL_ev if (i % w) >= edge else lo for i in range(h * w)]
        B, D = bilateral_base(L, h, w, self.CFG)
        y = h // 2
        worst = 0.0
        scale = 0.0
        for j in range(1, 4):
            p = y * w + (edge - j)
            q = y * w + (edge + j - 1)
            wp = B[p] - L[p]
            wq = B[q] - L[q]
            worst = max(worst, abs(wp + wq))
            scale = max(scale, abs(wp), abs(wq))
        return worst, scale

    def test_mirror_pairs_cancel_exactly(self):
        for dL in (0.5, 1.0, 2.0, 6.3, 12.0):
            worst, scale = self._mirror_residual(dL)
            self.assertLessEqual(worst, 1e-12,
                                 "weights do not cancel at %.1f EV" % dL)

    def test_cancellation_is_non_trivial_at_moderate_steps(self):
        """A 0.5 EV step must carry real weight on both sides."""
        worst, scale = self._mirror_residual(0.5)
        self.assertGreater(scale, 1e-2,
                           "expected non-negligible guidance weight")
        self.assertLessEqual(worst / scale, 1e-12)

    def test_strong_steps_collapse_both_sides(self):
        """At 6.3 EV the cross-edge weight underflows: no halo mechanism."""
        worst, scale = self._mirror_residual(6.3)
        self.assertLessEqual(scale, 1e-12)

    def test_whole_side_sum_is_zero(self):
        """Sum the weights over both complete sides of the edge, not just
        mirror pairs: the total must vanish."""
        from hdr_tm.guided import bilateral_base
        h = w = 40
        edge = 20
        lo = math.log2(0.18)
        L = [lo + 0.5 if (i % w) >= edge else lo for i in range(h * w)]
        B, _D = bilateral_base(L, h, w, self.CFG)
        left = sum(B[i] - L[i] for i in range(h * w) if (i % w) < edge)
        right = sum(B[i] - L[i] for i in range(h * w) if (i % w) >= edge)
        mag = sum(abs(B[i] - L[i]) for i in range(h * w))
        self.assertGreater(mag, 1e-3, "weights are all zero: trivial test")
        self.assertLessEqual(abs(left + right) / mag, 1e-9,
                             "two sided guidance weight sum is not zero")


class HaloImageTest(unittest.TestCase):
    TOL = 0.005           # display units of deviation allowed per side

    def _check(self, lo, hi):
        img, edge = synth.step_edge(h=48, w=48, lo=lo, hi=hi)
        res = run({"hdr": img, "tile": 16, "radius": 3})
        h, w = len(img), len(img[0])
        mid = h // 2
        row = res["out"][mid]
        dark_far = row[2]
        bright_far = row[w - 3]
        dark_dev = max(abs(row[c] - dark_far) for c in range(0, edge - 1))
        bright_dev = max(abs(row[c] - bright_far)
                         for c in range(edge + 1, w))
        self.assertLessEqual(dark_dev, self.TOL,
                             "halo on the dark side: %.3e" % dark_dev)
        self.assertLessEqual(bright_dev, self.TOL,
                             "dark rim on the bright side: %.3e" % bright_dev)
        # and the edge itself must stay a clean step, not a gradient
        self.assertGreater(row[edge] - row[edge - 1], 0.2)
        return dark_dev, bright_dev

    def test_strong_edge(self):
        self._check(0.02, 1.6)          # ~6.3 EV

    def test_extreme_edge(self):
        self._check(1e-3, 40.0)         # ~15 EV

    def test_moderate_edge(self):
        self._check(0.05, 0.6)          # ~3.6 EV, textured regime


class HaloMetricTest(unittest.TestCase):
    """Explicit scalar halo metric, reported so the acceptance number is
    reproducible rather than hidden inside an assertion.

        halo = max over both sides of |display(near edge) - display(far field)|

    Normalised by the edge's own contrast, so 0 means "the step has no
    overshoot or undershoot at all" and 1 would mean a full inversion.
    """

    LIMIT = 0.02

    def _halo(self, lo, hi):
        img, edge = synth.step_edge(h=64, w=64, lo=lo, hi=hi)
        res = run({"hdr": img, "tile": 16, "radius": 3})
        h, w = len(img), len(img[0])
        mid = h // 2
        row = res["out"][mid]
        contrast = row[edge] - row[edge - 1]
        self.assertGreater(contrast, 0.0)
        dark = max(abs(row[c] - row[2]) for c in range(0, edge - 1))
        bright = max(abs(row[c] - row[w - 3]) for c in range(edge + 1, w))
        return max(dark, bright) / contrast, dark, bright, contrast, res

    def test_halo_metric_below_threshold(self):
        for lo, hi in ((0.02, 1.6), (1e-3, 40.0), (0.05, 0.6)):
            halo, dark, bright, contrast, _res = self._halo(lo, hi)
            self.assertLessEqual(
                halo, self.LIMIT,
                "halo metric %.3e exceeds %.3f (step %.2f..%.2f)"
                % (halo, self.LIMIT, lo, hi))
            self.assertLessEqual(dark, contrast * self.LIMIT)
            self.assertLessEqual(bright, contrast * self.LIMIT)

    def test_halo_metric_is_near_zero(self):
        """The measured value is at rounding level, not merely under 0.02."""
        halo, dark, bright, _c, _res = self._halo(0.02, 1.6)
        self.assertLessEqual(halo, 1e-12)
        self.assertLessEqual(dark, 1e-12)
        self.assertLessEqual(bright, 1e-12)

    def test_highlight_rolloff_has_no_undershoot(self):
        """A very bright side must not be dragged down next to the edge."""
        img, edge = synth.step_edge(h=48, w=48, lo=1e-4, hi=1e4)
        res = run({"hdr": img, "tile": 16, "radius": 3})
        row = res["out"][24]
        far = row[45]
        for c in range(edge, 48):
            self.assertGreaterEqual(row[c], far - 1e-9)


class HaloVsBaselineTest(unittest.TestCase):
    def test_baseline_box_filter_does_halo(self):
        """Negative control: a plain box base layer *does* produce a halo.

        If this ever stops being true the halo test above has lost its
        meaning, so it is asserted explicitly.
        """
        img, edge = synth.step_edge(h=48, w=48, lo=0.02, hi=1.6)
        h, w = len(img), len(img[0])
        L = [[math.log2(v) for v in row] for row in img]

        # plain 7x7 box mean of the log luma
        R = 3
        base = [[0.0] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                y0, y1 = max(0, y - R), min(h - 1, y + R)
                x0, x1 = max(0, x - R), min(w - 1, x + R)
                s = 0.0
                n = 0
                for yy in range(y0, y1 + 1):
                    for xx in range(x0, x1 + 1):
                        s += L[yy][xx]
                        n += 1
                base[y][x] = s / n

        mid = h // 2
        # the box base is pulled towards the bright side next to the edge
        far = base[mid][2]
        near = base[mid][edge - 1]
        self.assertGreater(abs(near - far), 0.5,
                           "expected a large halo from the box baseline")


if __name__ == "__main__":
    unittest.main()