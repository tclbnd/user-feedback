"""Range conventions, quantisation and clipping policy."""

import unittest
from fractions import Fraction

from yuvkit.ranges import (FULL, LIMITED, RangeError, canonical_range, clip,
                           get_range, quantise, round_half_up)


class TestLayout(unittest.TestCase):
    def test_full(self):
        self.assertEqual((FULL.y_min, FULL.y_max), (0, 255))
        self.assertEqual((FULL.c_min, FULL.c_max), (0, 255))
        self.assertEqual((FULL.rgb_min, FULL.rgb_max), (0, 255))
        self.assertEqual(FULL.c_zero, 128)

    def test_limited(self):
        self.assertEqual((LIMITED.y_min, LIMITED.y_max), (16, 235))
        self.assertEqual((LIMITED.c_min, LIMITED.c_max), (16, 240))
        self.assertEqual((LIMITED.rgb_min, LIMITED.rgb_max), (16, 235))
        self.assertEqual(LIMITED.c_zero, 128)

    def test_spans(self):
        self.assertEqual(FULL.y_span, 255)
        self.assertEqual(LIMITED.y_span, 219)
        self.assertEqual(LIMITED.c_span, 224)

    def test_normalisation_endpoints(self):
        self.assertEqual(LIMITED.y_to_norm(16), 0)
        self.assertEqual(LIMITED.y_to_norm(235), 1)
        self.assertEqual(FULL.y_to_norm(0), 0)
        self.assertEqual(FULL.y_to_norm(255), 1)
        self.assertEqual(LIMITED.c_to_norm(128), 0)
        self.assertEqual(FULL.c_to_norm(128), 0)

    def test_chroma_normalisation_range(self):
        self.assertEqual(LIMITED.c_to_norm(16), Fraction(-1, 2))
        self.assertEqual(LIMITED.c_to_norm(240), Fraction(1, 2))
        self.assertEqual(FULL.c_to_norm(0), Fraction(-128, 255))

    def test_roundtrip_normalisation_exact(self):
        for rng in (FULL, LIMITED):
            for code in range(rng.legal_lo, rng.legal_hi + 1):
                if rng.y_in_range(code):
                    self.assertEqual(rng.norm_to_y(rng.y_to_norm(code)), code)
                if rng.c_in_range(code):
                    self.assertEqual(rng.norm_to_c(rng.c_to_norm(code)), code)


class TestAliases(unittest.TestCase):
    def test_names(self):
        self.assertEqual(canonical_range("tv"), "limited")
        self.assertEqual(canonical_range("PC"), "full")
        self.assertEqual(canonical_range("video"), "limited")
        self.assertEqual(get_range("full"), FULL)

    def test_unknown(self):
        with self.assertRaises(RangeError):
            canonical_range("studio-swing")


class TestRounding(unittest.TestCase):
    def test_half_up_not_half_even(self):
        cases = {
            Fraction(1, 2): 1,
            Fraction(3, 2): 2,
            Fraction(5, 2): 3,
            Fraction(-1, 2): 0,
            Fraction(-3, 2): -1,
            Fraction(7, 3): 2,
            Fraction(-7, 3): -2,
            Fraction(255): 255,
        }
        for value, want in cases.items():
            self.assertEqual(round_half_up(value), want, value)

    def test_matches_float_round_at_boundaries(self):
        # 2.5 -> 3 while Python's round() would give 2: the tie rule
        # matters because both engines must agree.
        self.assertEqual(round_half_up(Fraction(5, 2)), 3)
        self.assertEqual(round(2.5), 2)


class TestClipping(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(clip(-3, 0, 255), 0)
        self.assertEqual(clip(300, 0, 255), 255)
        self.assertEqual(clip(17, 16, 235), 17)

    def test_quantise_flags(self):
        low = quantise(Fraction(-3, 2), 0, 255)
        self.assertEqual(low.code, 0)
        self.assertTrue(low.clipped)
        self.assertEqual(low.raw, Fraction(-3, 2))
        high = quantise(Fraction(300), 16, 235)
        self.assertEqual(high.code, 235)
        self.assertTrue(high.clipped)
        fine = quantise(Fraction(100), 16, 235)
        self.assertEqual(fine.code, 100)
        self.assertFalse(fine.clipped)

    def test_clipping_is_not_silent(self):
        # The unrounded value survives so the caller can explain the clip.
        q = quantise(Fraction(-1000), 0, 255)
        self.assertEqual(q.code, 0)
        self.assertLessEqual(q.raw, 0)


if __name__ == "__main__":
    unittest.main()

