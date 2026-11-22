"""Spec-derived behaviour: values of acTL/fcTL fields, delays, sequence, regions."""

import struct
import unittest

from apngkit.apng import parse

from anims import SIG, actl, apng, chunk, fctl, fdat, ihdr, idat


class SpecTests(unittest.TestCase):
    def test_zero_delay_den_is_hundredths(self):
        # delay_den == 0 means 1/100 s, so a 100/0 delay is one second.
        out = parse(apng(2, delays=[(100, 0), (1, 0)]))
        self.assertEqual(out["delays"], ["1", "1/100"])

    def test_endless_plays(self):
        self.assertIsNone(parse(apng(2, plays=0))["plays"])
        self.assertEqual(parse(apng(2, plays=7))["plays"], 7)

    def test_frame_count_is_per_fctl(self):
        # One IDAT frame plus N fdAT frames is N + 1 frames.
        self.assertEqual(parse(apng(5))["frames"], 5)

    def test_dispose_names(self):
        out = parse(apng(3, disposes=[0, 1, 2]))
        self.assertEqual(out["dispose"], ["none", "background", "previous"])

    def test_blend_names(self):
        data = SIG + ihdr() + actl(2)
        data += fctl(0, blend=0) + idat()
        data += fctl(1, blend=1) + fdat(2)
        self.assertEqual(parse(data + chunk("IEND", b""))["blend"], ["source", "over"])

    def test_duration_sums_delays(self):
        out = parse(apng(3, delays=[(1, 10), (2, 10), (3, 10)]))
        self.assertEqual(out["duration"], "3/5")

    def test_duration_mixes_denominators(self):
        out = parse(apng(2, delays=[(1, 3), (1, 6)]))
        self.assertEqual(out["duration"], "1/2")

    def test_crc_errors_are_counted(self):
        good = chunk("acTL", struct.pack(">II", 1, 0))
        bad = good[:-4] + struct.pack(">I", 0xDEADBEEF)
        data = SIG + ihdr() + bad + fctl(0) + idat() + chunk("IEND", b"")
        self.assertEqual(parse(data)["crc_errors"], 1)

    def test_sequence_numbers_must_be_dense(self):
        data = SIG + ihdr() + actl(2) + fctl(0) + idat()
        data += fctl(2) + fdat(3) + chunk("IEND", b"")   # skipped 1
        self.assertFalse(parse(data)["sequence_ok"])

    def test_sequence_includes_fdat(self):
        data = SIG + ihdr() + actl(2) + fctl(0) + idat()
        data += fctl(1) + fdat(1) + chunk("IEND", b"")   # fdAT must be 2
        self.assertFalse(parse(data)["sequence_ok"])

    def test_regions_inside_canvas(self):
        data = SIG + ihdr(64, 64) + actl(2)
        data += fctl(0, w=64, h=64) + idat()
        data += fctl(1, w=16, h=16, x=48, y=48) + fdat(2) + chunk("IEND", b"")
        self.assertTrue(parse(data)["regions_ok"])

    def test_region_outside_canvas(self):
        data = SIG + ihdr(64, 64) + actl(1) + fctl(0, w=16, h=16, x=56, y=0) + idat()
        self.assertFalse(parse(data + chunk("IEND", b""))["regions_ok"])

    def test_empty_region(self):
        data = SIG + ihdr(64, 64) + actl(1) + fctl(0, w=0, h=16) + idat()
        self.assertFalse(parse(data + chunk("IEND", b""))["regions_ok"])

    def test_default_image_not_in_animation(self):
        data = SIG + ihdr() + actl(2) + idat()
        data += fctl(0) + fdat(1) + chunk("IEND", b"")
        out = parse(data)
        self.assertFalse(out["default_image_in_animation"])
        self.assertEqual(out["frames"], 1)

    def test_default_image_in_animation(self):
        self.assertTrue(parse(apng(2))["default_image_in_animation"])


if __name__ == "__main__":
    unittest.main()