import unittest

from ltckit.ltc import decode

from takes import ltc_wav


class BasicTests(unittest.TestCase):
    def test_mono(self):
        out = decode(ltc_wav((1, 0, 0, 0), 10, 25, lead=0.1))
        self.assertEqual((out["fps"], out["frames"], out["last"]), ("25", 10, "01:00:00:09"))

    def test_rate(self):
        out = decode(ltc_wav((0, 10, 0, 0), 6, 30, 44100, lead=0.1))
        self.assertEqual((out["sample_rate"], out["fps"], out["first"]), (44100, "30", "00:10:00:00"))

    def test_stereo_uses_last_channel(self):
        out = decode(ltc_wav(n=8, channels=2))
        self.assertEqual((out["channels"], out["frames"], out["first"]), (2, 8, "01:00:00:00"))

    def test_drop_frame(self):
        out = decode(ltc_wav(start=(10, 0, 0, 0), n=8, fps=30, drop=True))
        self.assertEqual((out["fps"], out["drop_frame"], out["first"], out["last"]),
                         ("30000/1001", True, "10:00:00;00", "10:00:00;07"))

    def test_drop_frame_across_minute(self):
        # a drop-frame clock skips ;00 and ;01 at the start of a minute
        out = decode(ltc_wav(start=(10, 0, 59, 28), n=5, fps=30, drop=True))
        self.assertEqual((out["first"], out["last"]), ("10:00:59;28", "10:01:00;02"))

    def test_24_bit(self):
        out = decode(ltc_wav(n=6, bits=24, lead=0.5))
        self.assertEqual((out["frames"], out["first"], out["last"]), (6, "01:00:00:00", "01:00:00:05"))

    def test_8_and_32_bit(self):
        for bits in (8, 32):
            out = decode(ltc_wav(n=5, bits=bits, lead=0.5))
            self.assertEqual(out["last"], "01:00:00:04", bits)

    def test_extensible_and_odd_chunk(self):
        out = decode(ltc_wav(n=6, extensible=True, odd_chunk=True, lead=0.5))
        self.assertEqual((out["frames"], out["last"]), (6, "01:00:00:05"))

    def test_reverse(self):
        # played backwards: reverse is set, and first/last follow the recording, so they descend
        out = decode(ltc_wav(n=8, reverse=True))
        self.assertTrue(out["reverse"])
        self.assertEqual((out["frames"], out["first"], out["last"]), (8, "01:00:00:07", "01:00:00:00"))

    def test_dc_offset_and_inversion(self):
        out = decode(ltc_wav(n=6, dc=5000, invert=True, lead=0.5))
        self.assertEqual((out["frames"], out["first"]), (6, "01:00:00:00"))

    def test_starts_mid_frame(self):
        # the frame cut off at the start of the recording does not count
        out = decode(ltc_wav(start=(5, 0, 0, 0), n=6, lead=0.62))
        self.assertEqual((out["frames"], out["first"]), (6, "05:00:00:00"))

    def test_user_bits_group_order(self):
        out = decode(ltc_wav(n=3, user=0x12345678, lead=0.5))
        self.assertEqual(out["user_bits"], "12345678")
