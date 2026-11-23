import math
import unittest

from lufskit.lufs import LufsError, kweighting_coeffs, layout_for, parse

from signals import channels_wav, segments_wav, sine_wav, stereo_sine


class FilterTests(unittest.TestCase):
    def test_48k_matches_standard_tables(self):
        shelf, rlb = kweighting_coeffs(48000)
        self._close(shelf, (1.53512485958697, -2.69169618940638,
                            1.19839281085285, -1.69065929318241,
                            0.73248077421585))
        self._close(rlb, (1.0, -2.0, 1.0, -1.99004759761517,
                          0.99007228115877))

    def test_low_frequencies_are_attenuated(self):
        # Full K-weighting (shelf + RLB), not just the shelf: a 30 Hz tone
        # must measure several LU below an equally loud 1 kHz tone.
        low = parse(sine_wav(1, 5, amp=0.2, freq=30.0))["integrated"]
        high = parse(sine_wav(1, 5, amp=0.2, freq=1000.0))["integrated"]
        self.assertLess(float(low) - float(high), -6.0)

    def test_filters_are_rate_agnostic(self):
        ref = parse(sine_wav(1, 4, amp=0.1, rate=48000))["integrated"]
        for rate in (11025, 22050, 32000, 44100, 96000):
            out = parse(sine_wav(1, 4, amp=0.1, rate=rate))
            self.assertAlmostEqual(float(out["integrated"]), float(ref),
                                   delta=0.3)

    def _close(self, got, expected):
        for a, b in zip(got, expected):
            self.assertAlmostEqual(a, b, places=10)


class GatingTests(unittest.TestCase):
    def test_relative_gate_excludes_quiet_passages(self):
        # Loud half at ~-17 LUFS, quiet half around -50 LUFS: the relative
        # gate must keep only the loud blocks instead of averaging both.
        out = parse(segments_wav([(5, 0.2), (5, 0.002)]))
        loud_only = parse(segments_wav([(5, 0.2)]))
        self.assertEqual(out["gated_blocks"], 50)
        self.assertAlmostEqual(float(out["integrated"]),
                               float(loud_only["integrated"]), delta=0.2)

    def test_silence_has_no_loudness(self):
        out = parse(stereo_sine(1.0, amp=0.0))
        for key in ("integrated", "momentary_max", "short_term_max",
                    "loudness_range", "sample_peak"):
            self.assertIsNone(out[key])
        self.assertEqual(out["gated_blocks"], 0)

    def test_blocks_hop_100ms_and_catch_bursts(self):
        # 200 ms burst: only a 400 ms / 100 ms hop window can contain it.
        data = segments_wav([(2.0, 0.0), (0.2, 0.5), (2.8, 0.0)])
        out = parse(data)
        self.assertIsNotNone(out["momentary_max"])
        self.assertGreater(float(out["momentary_max"]), -35.0)
        # Five 400 ms windows overlap the 200 ms burst (starts 1.7..2.1 s).
        self.assertEqual(out["gated_blocks"], 5)

    def test_absolute_gate_is_minus_70_and_strict(self):
        # Calibrate an amplitude around the -70 LUFS boundary, then check
        # blocks at or below it are rejected while louder blocks are kept.
        reference = float(parse(sine_wav(2, 3.0, amp=0.1,
                                          freq=1000.0))["integrated"])
        boundary = 0.1 * 10 ** ((-70 - reference) / 20)
        below = parse(sine_wav(2, 3.0, amp=boundary * 0.5, freq=1000.0))
        above = parse(sine_wav(2, 3.0, amp=boundary * 2.0, freq=1000.0))
        self.assertIsNone(below["integrated"])
        self.assertEqual(below["gated_blocks"], 0)
        self.assertIsNotNone(above["integrated"])
        self.assertGreater(above["gated_blocks"], 0)


class ChannelTests(unittest.TestCase):
    def test_lfe_is_never_measured(self):
        # Mask 0x3F order is FL FR FC LFE BL BR: only slot 3 (LFE) changes.
        rate = 48000
        n = int(3 * rate)
        tone = [0.1 * math.sin(2 * math.pi * 997 * i / rate)
                for i in range(n)]
        lfe_quiet = [0.0] * n
        lfe_loud = [0.9 * math.sin(2 * math.pi * 60 * i / rate)
                    for i in range(n)]
        base = parse(channels_wav(
            [tone, tone, tone, lfe_quiet, tone, tone],
            rate=rate, mask=0x3F))
        with_lfe = parse(channels_wav(
            [tone, tone, tone, lfe_loud, tone, tone],
            rate=rate, mask=0x3F))
        self.assertEqual(with_lfe["channel_names"],
                         ["FL", "FR", "FC", "LFE", "BL", "BR"])
        self.assertEqual(with_lfe["measured_channels"],
                         ["FL", "FR", "FC", "BL", "BR"])
        for key in ("integrated", "momentary_max", "short_term_max",
                    "loudness_range"):
            self.assertEqual(with_lfe[key], base[key])

        # LFE alone leaves nothing to measure.
        silent = segments_wav([(2.0, 0.0)], channels=6, mask=0x3F)
        out = parse(silent)
        self.assertIsNone(out["integrated"])
        self.assertIsNone(out["sample_peak"])

    def test_surround_weight_1_41(self):
        # Same signal in one front channel vs one surround channel: BS.1770-4
        # table 3 applies the 1.41 factor to mean-square, i.e. +10log10(1.41).
        front = parse(sine_wav(1, 4, amp=0.1, mask=0x1))["integrated"]
        surround = parse(sine_wav(1, 4, amp=0.1, mask=0x400))["integrated"]
        self.assertAlmostEqual(float(surround) - float(front),
                               10 * math.log10(1.41), delta=0.2)

    def test_mask_drives_channel_roles(self):
        self.assertEqual(layout_for(6, 0x3F),
                         ["FL", "FR", "FC", "LFE", "BL", "BR"])
        self.assertEqual(layout_for(2, 0x33),
                         ["FL", "FR", "BL", "BR", "SL", "SR"][:2])
        self.assertEqual(layout_for(1, 0), ["FC"])
        self.assertEqual(layout_for(2, 0), ["FL", "FR"])
        self.assertEqual(layout_for(6, 0),
                         ["FL", "FR", "FC", "LFE", "SL", "SR"])
        with self.assertRaises(LufsError):
            layout_for(3, 0)

    def test_masked_51_names_and_lfe_exclusion(self):
        out = parse(sine_wav(6, 3.0, amp=0.1, mask=0x3F))
        self.assertEqual(out["channel_names"],
                         ["FL", "FR", "FC", "LFE", "BL", "BR"])
        self.assertNotIn("LFE", out["measured_channels"])


class FormatTests(unittest.TestCase):
    def test_pcm_and_float_agree(self):
        ref = parse(sine_wav(2, 3.0, amp=0.1, bits=16))
        for bits in (24, 32):
            out = parse(sine_wav(2, 3.0, amp=0.1, bits=bits))
            self.assertEqual(out["integrated"], ref["integrated"])
            self.assertAlmostEqual(float(out["sample_peak"]),
                                   float(ref["sample_peak"]), delta=0.1)

    def test_output_contract(self):
        out = parse(stereo_sine(6.0))
        self.assertEqual(out["sample_rate"], 48000)
        self.assertEqual(out["channels"], 2)
        self.assertEqual(out["channel_names"], ["FL", "FR"])
        self.assertEqual(out["measured_channels"], ["FL", "FR"])
        self.assertEqual(out["duration"], "6.000")
        self.assertIsInstance(out["gated_blocks"], int)
        for key in ("sample_peak", "momentary_max", "short_term_max",
                    "integrated", "loudness_range"):
            self.assertIsInstance(out[key], str)

    def test_loudness_range_zero_for_constant_programme(self):
        out = parse(sine_wav(6, 6.0, amp=0.1))
        self.assertEqual(out["loudness_range"], "0.0")
