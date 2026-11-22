import unittest
from fractions import Fraction

from dtskit.dts import scan

from streams import frame, stream


class BasicTests(unittest.TestCase):
    def test_single_frame(self):
        out = scan(frame(rate=24000, amode=9))
        self.assertEqual((out["layout"], out["frames"], out["sample_rate"], out["channels"]), ("BE16", 1, 24000, 5))

    def test_bitrate(self):
        self.assertEqual(scan(frame(kbps_index=24))["bitrate_kbps"], 1536)

    def test_four_layouts(self):
        for layout in ("BE16", "LE16", "BE14", "LE14"):
            with self.subTest(layout=layout):
                out = scan(stream(3, layout=layout))
                self.assertEqual((out["layout"], out["frames"]), (layout, 3))

    def test_all_frames_counted(self):
        self.assertEqual(scan(stream(7))["frames"], 7)

    def test_14bit_spare_bits_ignored(self):
        for spare in range(4):
            with self.subTest(spare=spare):
                self.assertEqual(scan(stream(2, layout="LE14", spare=spare))["frames"], 2)

    def test_samples_per_frame_is_biased(self):
        out = scan(frame(nblks=15))
        self.assertEqual(out["samples_per_frame"], 16 * 32)

    def test_sample_rates_not_swapped(self):
        self.assertEqual(scan(frame(rate=44100))["sample_rate"], 44100)
        self.assertEqual(scan(frame(rate=48000))["sample_rate"], 48000)

    def test_lfe_flag(self):
        self.assertTrue(scan(frame(lfe=True))["lfe"])
        self.assertFalse(scan(frame(lfe=False))["lfe"])

    def test_channels_without_lfe(self):
        self.assertEqual(scan(frame(amode=9, lfe=True))["channels"], 5)

    def test_duration_counts_samples(self):
        out = scan(stream(3, nblks=7, rate=48000))
        self.assertEqual(out["samples_per_frame"], 256)
        self.assertEqual(out["duration"], str(Fraction(3 * 256, 48000)))

    def test_junk_and_header_skipped(self):
        data = b"RIFF" + b"\x00" * 40 + stream(2) + b"junkjunk" + stream(3)
        self.assertEqual(scan(data)["frames"], 5)

    def test_truncated_last_frame_dropped(self):
        self.assertEqual(scan(stream(2) + frame()[:900])["frames"], 2)

    def test_variable_frame_size(self):
        self.assertTrue(scan(stream(1, fsize=2015) + stream(1, fsize=1023))["variable_frame_size"])
        self.assertFalse(scan(stream(2, fsize=2015))["variable_frame_size"])

    def test_extensions(self):
        self.assertEqual(scan(frame(ext_id=6))["extensions"], ["XXCh"])

    def test_open_bitrate(self):
        self.assertIsNone(scan(frame(kbps_index=31))["bitrate_kbps"])
