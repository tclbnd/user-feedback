"""Exact matrix constants, quantisation and the anti-mixing invariants."""

import unittest
from fractions import Fraction

from yuvkit.matrix import (DEN, MATRICES, ColorMatrix, MixedStandardError,
                           get_matrix, quantise_ratio)


class TestConstants(unittest.TestCase):
    def test_denominator_represents_every_standard_exactly(self):
        for name, std in (("bt601", (2990, 1140)), ("bt709", (2126, 722)),
                          ("bt2020", (2627, 593))):
            m = get_matrix(name)
            self.assertEqual(m.krn, std[0])
            self.assertEqual(m.kbn, std[1])
            self.assertEqual(m.krn + m.kgn + m.kbn, DEN)

    def test_gains_derived_from_same_constants(self):
        for name in ("bt601", "bt709", "bt2020"):
            m = get_matrix(name)
            self.assertEqual(Fraction(m.cb_num, m.cb_den),
                             1 / (2 * (1 - Fraction(m.kbn, DEN))))
            self.assertEqual(Fraction(m.cr_num, m.cr_den),
                             1 / (2 * (1 - Fraction(m.krn, DEN))))

    def test_validate_rejects_borrowed_constants(self):
        m = get_matrix("bt709")
        mixed = ColorMatrix.__new__(ColorMatrix)
        mixed.standard = m.standard
        mixed.krn, mixed.kbn = 2990, 1140       # BT.601 weights
        mixed.kgn = DEN - mixed.krn - mixed.kbn
        mixed.cb_num, mixed.cb_den = m.cb_num, m.cb_den
        mixed.cr_num, mixed.cr_den = m.cr_num, m.cr_den
        with self.assertRaises(MixedStandardError):
            mixed.validate()

    def test_validate_rejects_wrong_gain_sign(self):
        m = get_matrix("bt709")
        broken = ColorMatrix.__new__(ColorMatrix)
        broken.standard = m.standard
        broken.krn, broken.kgn, broken.kbn = m.krn, m.kgn, m.kbn
        # the classic 0.5/(1+K) mistake
        broken.cb_num = 5000
        broken.cb_den = 10000 + broken.kbn
        broken.cr_num, broken.cr_den = m.cr_num, m.cr_den
        with self.assertRaises(MixedStandardError):
            broken.validate()

    def test_all_registered_matrices_validate(self):
        for m in MATRICES.values():
            self.assertTrue(m.validate())


class TestQuantiseRatio(unittest.TestCase):
    def test_ties_up(self):
        self.assertEqual(quantise_ratio(5, 2), 3)
        self.assertEqual(quantise_ratio(-5, 2), -2)
        self.assertEqual(quantise_ratio(7, 2), 4)
        self.assertEqual(quantise_ratio(-7, 2), -3)

    def test_exact(self):
        self.assertEqual(quantise_ratio(255, 1), 255)
        self.assertEqual(quantise_ratio(254, 2), 127)

    def test_negative_denominator(self):
        self.assertEqual(quantise_ratio(-5, -2), 3)


class TestForwardKnownValues(unittest.TestCase):
    """Standard truth values for the limited-range 8-bit conventions."""

    def codes(self, std, pixel, rgb_range="full", yuv_range="limited"):
        raw = get_matrix(std).forward_raw(*pixel, rgb_range, yuv_range)
        return tuple(r.code for r in raw)

    def test_black_white(self):
        for std in ("bt601", "bt709", "bt2020"):
            self.assertEqual(self.codes(std, (0, 0, 0)), (16, 128, 128))
            self.assertEqual(self.codes(std, (255, 255, 255)),
                             (235, 128, 128))

    def test_grey_is_neutral_chroma(self):
        for v in range(0, 256, 17):
            _y, cb, cr = self.codes("bt709", (v, v, v))
            self.assertEqual(cb, 128)
            self.assertEqual(cr, 128)

    def test_bt709_primaries(self):
        # Canonical 8-bit BT.709 limited range values.
        self.assertEqual(self.codes("bt709", (255, 0, 0)), (63, 102, 240))
        self.assertEqual(self.codes("bt709", (0, 255, 0)), (173, 42, 26))
        self.assertEqual(self.codes("bt709", (0, 0, 255)), (32, 240, 118))

    def test_bt601_primaries_differ_from_709(self):
        for pixel in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
            self.assertNotEqual(self.codes("bt601", pixel),
                                self.codes("bt709", pixel))

    def test_full_range_black_white(self):
        self.assertEqual(self.codes("bt709", (0, 0, 0), "full", "full"),
                         (0, 128, 128))
        self.assertEqual(self.codes("bt709", (255, 255, 255), "full",
                                    "full"), (255, 128, 128))

    def test_luma_only_depends_on_luma(self):
        # Y' of the saturated primaries is Kr, Kg, Kb of full scale.
        y_r = self.codes("bt709", (255, 0, 0), "full", "full")[0]
        self.assertIn(y_r, (54, 55))  # 0.2126*255 = 54.2
        y_b = self.codes("bt709", (0, 0, 255), "full", "full")[0]
        self.assertEqual(y_b, 18)     # 0.0722*255 = 18.4

    def test_limited_rgb_input_is_normalised_before_the_matrix(self):
        m = get_matrix("bt709")
        raw = m.forward_raw(235, 235, 235, "limited", "limited")
        self.assertEqual([r.code for r in raw], [235, 128, 128])
        raw = m.forward_raw(16, 16, 16, "limited", "limited")
        self.assertEqual([r.code for r in raw], [16, 128, 128])


class TestInverseKnownValues(unittest.TestCase):
    def test_roundtrip_of_the_primaries(self):
        m = get_matrix("bt709")
        for pixel in ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0),
                      (0, 0, 255), (18, 52, 86), (200, 100, 50)):
            yuv = [r.code for r in m.forward_raw(*pixel, "full", "limited")]
            back = [r.code for r in m.inverse_raw(*yuv, "limited", "full")]
            self.assertLessEqual(max(abs(a - b) for a, b in zip(pixel, back)),
                                 2, (pixel, yuv, back))

    def test_inverse_of_neutral_is_neutral(self):
        m = get_matrix("bt601")
        for y in range(16, 236, 10):
            rgb = [r.code for r in m.inverse_raw(y, 128, 128, "limited",
                                                 "full")]
            self.assertEqual(rgb[0], rgb[1])
            self.assertEqual(rgb[1], rgb[2])


class TestRawCode(unittest.TestCase):
    def test_clipping_flag(self):
        from yuvkit.matrix import RawCode
        low = RawCode(-1000, 1, 0, 255)
        self.assertEqual(low.code, 0)
        self.assertTrue(low.clipped)
        high = RawCode(1000, 1, 0, 255)
        self.assertEqual(high.code, 255)
        self.assertTrue(high.clipped)
        ok = RawCode(1000, 10, 0, 255)
        self.assertEqual(ok.code, 100)
        self.assertFalse(ok.clipped)

    def test_boundary_distance(self):
        from yuvkit.matrix import RawCode
        # A value exactly on a .5 boundary has zero distance to it.
        self.assertAlmostEqual(RawCode(1, 2, 0, 255).boundary_distance(), 0.0)
        self.assertAlmostEqual(RawCode(3, 2, 0, 255).boundary_distance(), 0.0)
        # 100.1 sits 0.4 away from the nearest boundary (100.5).
        self.assertAlmostEqual(RawCode(1001, 10, 0, 255).boundary_distance(),
                               0.4)
        self.assertAlmostEqual(RawCode(0, 1, 0, 255).boundary_distance(), 0.5)


if __name__ == "__main__":
    unittest.main()

