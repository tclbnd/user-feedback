"""Standards registry: constants, aliases, and the anti-mixing guard."""

import unittest
from fractions import Fraction

from yuvkit.standards import (STANDARDS, Standard, UnknownStandard,
                              canonical_name, get_standard)


class TestConstants(unittest.TestCase):
    def test_kr_kb_match_the_recommendations(self):
        self.assertEqual(get_standard("bt601").kr, Fraction(299, 1000))
        self.assertEqual(get_standard("bt601").kb, Fraction(114, 1000))
        self.assertEqual(get_standard("bt709").kr, Fraction(2126, 10000))
        self.assertEqual(get_standard("bt709").kb, Fraction(722, 10000))
        self.assertEqual(get_standard("bt2020").kr, Fraction(2627, 10000))
        self.assertEqual(get_standard("bt2020").kb, Fraction(593, 10000))

    def test_weights_normalise(self):
        for std in STANDARDS.values():
            self.assertEqual(std.kr + std.kg + std.kb, 1)

    def test_bt709_does_not_borrow_bt601(self):
        # The original defect: K["bt709"] was a copy of K["bt601"].
        self.assertNotEqual(get_standard("bt709").kr,
                            get_standard("bt601").kr)
        self.assertNotEqual(get_standard("bt709").kb,
                            get_standard("bt601").kb)

    def test_gains_use_the_correct_formula(self):
        for std in STANDARDS.values():
            self.assertEqual(std.cb_gain, 1 / (2 * (1 - std.kb)))
            self.assertEqual(std.cr_gain, 1 / (2 * (1 - std.kr)))
            # and specifically not the 1/(2(1+K)) variant
            self.assertNotEqual(std.cb_gain, 1 / (2 * (1 + std.kb)))

    def test_inverse_gains_are_reciprocal(self):
        for std in STANDARDS.values():
            self.assertEqual(std.cb_gain * std.cb_inverse_gain, 1)
            self.assertEqual(std.cr_gain * std.cr_inverse_gain, 1)

    def test_luma_of_white_is_one(self):
        for std in STANDARDS.values():
            self.assertEqual(std.luma(1, 1, 1), 1)

    def test_luma_of_primitives(self):
        std = get_standard("bt601")
        self.assertEqual(std.luma(1, 0, 0), std.kr)
        self.assertEqual(std.luma(0, 0, 1), std.kb)


class TestAliases(unittest.TestCase):
    def test_aliases(self):
        cases = {
            "rec601": "bt601",
            "BT.601": "bt601",
            "ITU-R BT.709": "bt709",
            "smpte170m": "bt601",
            "REC-2020": "bt2020",
        }
        for alias, canonical in cases.items():
            self.assertEqual(canonical_name(alias), canonical, alias)

    def test_standard_object_accepted(self):
        std = get_standard("bt709")
        self.assertEqual(canonical_name(std), "bt709")

    def test_unknown_rejected(self):
        for bad in ("bt470", "", None, 42, "rec2100"):
            with self.assertRaises(UnknownStandard):
                canonical_name(bad)

    def test_registry_is_cached(self):
        self.assertIs(get_standard("bt709"), get_standard("709"))


if __name__ == "__main__":
    unittest.main()

