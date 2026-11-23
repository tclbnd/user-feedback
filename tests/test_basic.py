import math
import unittest

from lufskit.lufs import parse

from signals import (SPEAKER_BL, SPEAKER_BR, SPEAKER_FC, SPEAKER_FL, SPEAKER_FR,
                     SPEAKER_LFE, SPEAKER_SL, SPEAKER_SR, programme, sine,
                     stereo_sine, wav)


def integrated(channels, **kw):
    return float(parse(wav(channels, **kw))["integrated"])


class BasicTests(unittest.TestCase):
    def test_header(self):
        out = parse(stereo_sine(1.0))
        self.assertEqual((out["sample_rate"], out["channels"], out["duration"]), (48000, 2, "1.000"))

    def test_silence(self):
        self.assertIsNone(parse(stereo_sine(1.0, amp=0.0))["integrated"])


class SampleRateTests(unittest.TestCase):
    """The K-weighting must be derived for the file's own sample rate."""

    def test_sine_loudness_is_rate_independent(self):
        for rate in (8000, 44100, 48000, 88200, 96000, 192000):
            with self.subTest(rate=rate):
                self.assertAlmostEqual(
                    float(parse(wav(sine(1000, 2.0, 0.1, rate, 2), rate))["integrated"]),
                    -20.0, delta=0.2)

    def test_48k_coefficients_match_the_standard(self):
        from lufskit import lufs
        for got, want in zip(lufs.shelf_coefficients(48000), lufs.SHELF_48K):
            self.assertAlmostEqual(got, want, places=9)
        for got, want in zip(lufs.highpass_coefficients(48000), lufs.HIGHPASS_48K):
            self.assertAlmostEqual(got, want, places=9)


class KWeightingTests(unittest.TestCase):
    """Low-frequency content must not read louder than the K-weighted truth."""

    def test_low_frequency_is_attenuated(self):
        mid = integrated(sine(1000, 3.0, 0.1, channels=2))
        low = integrated(sine(50, 3.0, 0.1, channels=2))
        self.assertGreater(mid - low, 3.0)

    def test_response_matches_the_standard_curve(self):
        import cmath
        from lufskit.lufs import highpass_coefficients, shelf_coefficients

        def gain(freq, rate=48000):
            z = cmath.exp(-2j * math.pi * freq / rate)
            g = 1
            for b0, b1, b2, a1, a2 in (shelf_coefficients(rate),
                                       highpass_coefficients(rate)):
                g *= (b0 + b1 * z + b2 * z * z) / (1 + a1 * z + a2 * z * z)
            return 20 * math.log10(abs(g))

        # BS.1770-4 figure 4: about -12 dB at 20 Hz, +4 dB above 2 kHz.
        self.assertAlmostEqual(gain(20), -13.3, delta=1.0)
        self.assertAlmostEqual(gain(100), -1.1, delta=0.5)
        self.assertAlmostEqual(gain(1000), 0.7, delta=0.3)
        self.assertAlmostEqual(gain(10000), 4.0, delta=0.3)

    def test_equal_loudness_at_any_rate(self):
        # A mid-band tone must read the same whatever the sample rate.
        values = [integrated(sine(1000, 2.0, 0.1, rate, 2), rate=rate)
                  for rate in (32000, 44100, 48000, 96000, 192000)]
        self.assertLess(max(values) - min(values), 0.6)


class GatingTests(unittest.TestCase):
    """EBU Tech 3341 cases 1-6."""

    def check(self, segments, expected):
        self.assertAlmostEqual(
            float(parse(wav(programme(segments, channels=2)))["integrated"]),
            expected, delta=0.1)

    def test_case_1_and_2(self):
        self.check([(-23, 20)], -23.0)
        self.check([(-33, 20)], -33.0)

    def test_case_3_quiet_pauses_are_gated(self):
        self.check([(-36, 10), (-23, 60), (-36, 10)], -23.0)

    def test_case_4_absolute_gate(self):
        # The -72 LUFS sections fall below the absolute gate; the -36 and -23
        # sections that remain average to -27.3 LUFS.
        self.check([(-72, 10), (-36, 10), (-23, 10), (-36, 10), (-72, 10)], -27.3)

    def test_case_5_relative_gate(self):
        self.check([(-26, 20), (-20, 20), (-26, 20)], -23.0)

    def test_case_6_level_steps(self):
        self.check([(-26, 20), (-20, 20), (-26, 20), (-20, 20), (-26, 20)], -22.6)

    def test_long_quiet_dialogue_does_not_drag_the_result_down(self):
        loud = float(parse(wav(programme([(-23, 30)], channels=2)))["integrated"])
        mixed = float(parse(wav(programme([(-23, 30), (-60, 60)], channels=2)))["integrated"])
        self.assertAlmostEqual(mixed, loud, delta=0.1)

    def test_gated_block_count(self):
        # A 10 s file gives 100 segments and therefore 100 - 4 + 1 blocks,
        # all of them above both gates.
        out = parse(wav(programme([(-23, 10)], channels=2)))
        self.assertEqual(out["gated_blocks"], 97)


class LayoutTests(unittest.TestCase):
    """Channel roles and weights."""

    def test_stereo_is_front_only(self):
        out = parse(wav(sine(1000, 2.0, 0.05, channels=2)))
        self.assertEqual(out["measured_channels"], 2)
        self.assertAlmostEqual(float(out["integrated"]), -26.0, delta=0.2)

    def test_mono_is_front(self):
        out = parse(wav(sine(1000, 2.0, 0.05, channels=1)))
        self.assertAlmostEqual(float(out["integrated"]), -29.0, delta=0.2)

    def test_back_channels_weigh_1_41(self):
        back = sine(1000, 2.0, 0.05, channels=2)
        silent = [0.0] * len(back[0])
        out = parse(wav([silent, silent] + back,
                        mask=SPEAKER_BL | SPEAKER_BR | SPEAKER_SL | SPEAKER_SR))
        expected = float(parse(wav(back, mask=SPEAKER_FL | SPEAKER_FR))["integrated"])
        self.assertAlmostEqual(float(out["integrated"]) - expected,
                               10 * math.log10(1.41), delta=0.2)

    def test_lfe_is_excluded(self):
        front = sine(1000, 2.0, 0.05, channels=2)
        lfe = sine(60, 2.0, 0.9)
        with_lfe = parse(wav(front + lfe,
                             mask=SPEAKER_FL | SPEAKER_FR | SPEAKER_LFE))
        without_lfe = parse(wav(front, mask=SPEAKER_FL | SPEAKER_FR))
        self.assertEqual(with_lfe["channels"], 3)
        self.assertEqual(with_lfe["measured_channels"], 2)
        self.assertEqual(with_lfe["integrated"], without_lfe["integrated"])

    def test_surround_weight_from_mask(self):
        front = sine(1000, 2.0, 0.05, channels=2)
        back = sine(1000, 2.0, 0.05, channels=2)
        with_back = parse(wav(front + back,
                              mask=SPEAKER_FL | SPEAKER_FR | SPEAKER_BL | SPEAKER_BR))
        without = parse(wav(front, mask=SPEAKER_FL | SPEAKER_FR))
        self.assertAlmostEqual(
            float(with_back["integrated"]) - float(without["integrated"]),
            10 * math.log10((2 + 2 * 1.41) / 2), delta=0.2)

    def test_51_without_mask_uses_the_usual_order(self):
        channels = [0.05 * math.sin(2 * math.pi * 1000 * i / 48000) for i in range(48000)]
        lfe = [0.9 * math.sin(2 * math.pi * 50 * i / 48000) for i in range(48000)]
        out = parse(wav([channels, channels, channels, lfe, channels, channels]))
        self.assertEqual(out["measured_channels"], 5)

    def test_51_mask_excludes_lfe_and_weighs_surround(self):
        tone = 0.05
        front = sine(1000, 2.0, tone, channels=3)
        lfe = sine(50, 2.0, 0.9)
        back = sine(1000, 2.0, tone, channels=2)
        mask = (SPEAKER_FL | SPEAKER_FR | SPEAKER_FC | SPEAKER_LFE
                | SPEAKER_BL | SPEAKER_BR)
        out = parse(wav(front + lfe + back, mask=mask))
        plain = parse(wav(front + back, mask=SPEAKER_FL | SPEAKER_FR | SPEAKER_FC
                          | SPEAKER_BL | SPEAKER_BR))
        self.assertEqual(out["measured_channels"], 5)
        self.assertEqual(out["integrated"], plain["integrated"])

    def test_mask_wins_over_the_default_layout(self):
        silent = [0.0] * 48000
        lfe = sine(60, 1.0, 0.9)[0]
        # The mask says the fourth channel is LFE, not the back-right of the
        # default 4-channel order; the file is therefore only three channels
        # of programme.
        out = parse(wav([silent, silent, silent, lfe],
                        mask=SPEAKER_FL | SPEAKER_FR | SPEAKER_FC | SPEAKER_LFE))
        self.assertEqual(out["measured_channels"], 3)
        self.assertIsNone(out["integrated"])


class BlockTests(unittest.TestCase):
    """Block timing and maxima."""

    def test_blocks_start_every_100ms(self):
        # A 10 s file gives 100 segments and 97 blocks (400 ms each).
        out = parse(wav(programme([(-23, 10)], channels=2)))
        self.assertEqual(out["gated_blocks"], 97)

    def test_momentary_window_is_400ms(self):
        out = parse(wav(programme([(-23, 0.4)], channels=2)))
        self.assertIsNotNone(out["momentary_max"])

    def test_short_burst_reaches_momentary_max(self):
        rate = 48000
        n = rate * 3
        burst = [0.5 * math.sin(2 * math.pi * 1000 * i / rate)
                 if int(1.2 * rate) <= i < int(1.3 * rate) else 0.0 for i in range(n)]
        out = parse(wav([burst, burst]))
        # A 0.5 sine in two channels, present for a quarter of the block.
        self.assertGreater(float(out["momentary_max"]), -13.0)
        self.assertGreater(float(out["momentary_max"]), float(out["short_term_max"]))

    def test_short_term_window_is_3s(self):
        out = parse(wav(programme([(-23, 2.9)], channels=2)))
        self.assertIsNone(out["short_term_max"])
        out = parse(wav(programme([(-23, 3.0)], channels=2)))
        self.assertIsNotNone(out["short_term_max"])


class RangeTests(unittest.TestCase):
    """EBU Tech 3342 loudness range cases."""

    def check(self, segments, expected):
        out = parse(wav(programme(segments, channels=2)))
        self.assertAlmostEqual(float(out["loudness_range"]), expected, delta=0.2)

    def test_range_cases(self):
        self.check([(-20, 20), (-30, 20)], 10.0)
        self.check([(-20, 20), (-15, 20)], 5.0)
        self.check([(-40, 20), (-20, 20)], 20.0)
        self.check([(-50, 20), (-35, 20), (-20, 20), (-35, 20), (-50, 20)], 15.0)

    def test_absolute_gate_removes_silence(self):
        # The digital-silence tail is below -70 LUFS, so it is excluded; only
        # the short-term blocks that straddle the transition remain variable.
        with_silence = parse(wav(programme([(-23, 10), (-100, 10)], channels=2)))
        self.assertLess(float(with_silence["loudness_range"]), 10.0)

    def test_constant_programme_has_zero_range(self):
        self.check([(-23, 10)], 0.0)


class FormatTests(unittest.TestCase):
    """Every sample format the deliveries use."""

    def test_bit_depths_agree(self):
        reference = None
        for bits in (8, 16, 24, 32):
            with self.subTest(bits=bits):
                value = float(parse(wav(sine(1000, 2.0, 0.1, channels=2), bits=bits))["integrated"])
                self.assertAlmostEqual(value, -20.0, delta=0.3)
                reference = value if reference is None else reference

    def test_float_formats_agree(self):
        for bits in (32, 64):
            with self.subTest(bits=bits):
                value = float(parse(wav(sine(1000, 2.0, 0.1, channels=2),
                                        bits=bits, float_samples=True))["integrated"])
                self.assertAlmostEqual(value, -20.0, delta=0.2)

    def test_extensible_pcm_header(self):
        out = parse(wav(sine(1000, 2.0, 0.1, channels=2), mask=SPEAKER_FL | SPEAKER_FR))
        self.assertAlmostEqual(float(out["integrated"]), -20.0, delta=0.2)

    def test_sample_peak(self):
        out = parse(wav([[-0.5, 0.0, 0.5, 0.0]]))
        self.assertAlmostEqual(float(out["sample_peak"]), 20 * math.log10(0.5), delta=0.2)


class ErrorTests(unittest.TestCase):
    def test_not_a_wav(self):
        with self.assertRaises(ValueError):
            parse(b"junk")

    def test_truncated_data_chunk(self):
        data = stereo_sine(1.0)
        out = parse(data[:-1000])
        self.assertEqual(out["channels"], 2)


if __name__ == "__main__":
    unittest.main()
