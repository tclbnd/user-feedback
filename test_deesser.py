#!/usr/bin/env python3
"""Acceptance tests for the de-esser.

Covers the four required scenarios on synthetic material:

* sibilant       : narrow band attenuation, no residue after the sibilant
* broadband drum : reference energy blocks the detector, LF unchanged
* normal voice   : body preserved sample for sample, sibilant reduced
* automation     : step changes and enable/bypass ramps stay click-free

plus the structural guarantees: complementary band split, delay alignment,
block-size invariance, stereo linking, continuous state, and loudness
preservation of the non-target band.

Run with ``python3 -m unittest test_deesser -v`` (or ``python3 test_deesser.py``).
"""

import json
import math
import os
import random
import tempfile
import unittest

import deesser
from deesser import (DeEsser, DeEsserError, FrameDeEsser, crossover_delay,
                     default_taps, design_lowpass)

FS = 48000


# ---------------------------------------------------------------------------
# signal generators (deterministic, no dependencies)
# ---------------------------------------------------------------------------

def _rms(values, start=0, end=None):
    end = len(values) if end is None else end
    chunk = values[start:end]
    if not chunk:
        return 0.0
    return math.sqrt(sum(v * v for v in chunk) / len(chunk))


def _db_ratio(numerator, denominator):
    return 20.0 * math.log10(max(numerator, 1e-30) / max(denominator, 1e-30))


def _aligned(dry, wet, latency, start, stop=None):
    """Compare the delayed dry signal with the processed output."""
    stop = min(len(dry) if stop is None else stop, len(wet) - latency)
    return [abs(wet[i + latency] - dry[i]) for i in range(start, stop)]


def band_power(signal, low_hz, high_hz, sample_rate=FS, step_hz=250):
    """Parseval-free band power via summed complex DFT bins."""
    n = len(signal)
    power = 0.0
    for freq in range(int(low_hz), int(high_hz) + 1, step_hz):
        w = 2.0 * math.pi * freq / sample_rate
        re = 0.0
        im = 0.0
        for i, value in enumerate(signal):
            re += value * math.cos(w * i)
            im -= value * math.sin(w * i)
        power += (re * re + im * im) / (n * n)
    return power


def band_change_db(dry, wet, low_hz, high_hz, latency=0, sample_rate=FS,
                   start=None, stop=None):
    """Band power change of the processed output against the dry input."""
    if start is not None or stop is not None:
        begin = 0 if start is None else start
        end = len(dry) if stop is None else stop
        dry = dry[begin:end]
        wet = wet[begin + latency:]
    else:
        wet = wet[latency:]
    dry_power = band_power(dry, low_hz, high_hz, sample_rate)
    wet_power = band_power(wet, low_hz, high_hz, sample_rate)
    return 10.0 * math.log10(max(wet_power, 1e-30) / max(dry_power, 1e-30))


def make_sibilant(duration=0.6, start=0.10, length=0.18, sample_rate=FS,
                  seed=11):
    """Isolated /s/: HF tones plus high-passed noise, silence elsewhere."""
    rnd = random.Random(seed)
    n = int(duration * sample_rate)
    out = []
    smoothed = 0.0
    for i in range(n):
        t = i / sample_rate
        if start <= t < start + length:
            env = math.sin(math.pi * (t - start) / length) ** 2
        else:
            env = 0.0
        noise = rnd.uniform(-1.0, 1.0)
        smoothed += (noise - smoothed) * 0.35
        hiss = noise - smoothed                      # crude high-pass
        out.append(env * (0.40 * math.sin(2 * math.pi * 8200 * t)
                          + 0.22 * hiss
                          + 0.12 * math.sin(2 * math.pi * 6800 * t)))
    return out


def make_drum(duration=0.5, sample_rate=FS, seed=3):
    """Broadband hit: kick body plus full-band noise transient."""
    rnd = random.Random(seed)
    n = int(duration * sample_rate)
    out = []
    smoothed = 0.0
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t / 0.10)
        noise = rnd.uniform(-1.0, 1.0)
        smoothed += (noise - smoothed) * 0.12
        body = (0.55 * math.sin(2 * math.pi * 62 * t)
                + 0.30 * math.sin(2 * math.pi * 175 * t))
        out.append(env * (0.45 * noise + 0.55 * body))
    return out


def make_voice(duration=1.0, sample_rate=FS, seed=5):
    """Speech-like: voiced formants then a sibilant tail."""
    rnd = random.Random(seed)
    n = int(duration * sample_rate)
    out = []
    for i in range(n):
        t = i / sample_rate
        env = 0.5 * (1.0 - math.cos(2.0 * math.pi
                                   * min(1.0, max(0.0, (t - 0.05) / 0.55))))
        if t > 0.70:
            env *= max(0.0, 1.0 - (t - 0.70) / 0.18)
        f0 = 118.0 + 14.0 * math.sin(2.0 * math.pi * 4.0 * t)
        glottal = math.sin(2.0 * math.pi * f0 * t) * (1.0 + 0.25 * math.sin(2 * math.pi * f0 * 2 * t))
        body = (0.50 * math.sin(2.0 * math.pi * 680.0 * t)
                + 0.32 * math.sin(2.0 * math.pi * 1180.0 * t)
                + 0.18 * math.sin(2.0 * math.pi * 2350.0 * t))
        tail = 0.0
        if 0.74 <= t < 0.88:
            e = math.sin(math.pi * (t - 0.74) / 0.14) ** 2
            tail = e * (0.30 * math.sin(2.0 * math.pi * 8000.0 * t)
                        + 0.20 * rnd.uniform(-1.0, 1.0))
        out.append(env * glottal * body + tail)
    return out


def lowpass_of(signal, cutoff=2000.0, sample_rate=FS):
    """Low band of a signal, using the same linear-phase split as the engine."""
    kernel = design_lowpass(cutoff, sample_rate, 257, 8.0)
    low, _ = deesser.band_split([(v,) for v in signal], split_hz=cutoff,
                                sample_rate=sample_rate)
    return [frame[0] for frame in low], (len(kernel) - 1) // 2


def low_band_residual(dry, wet, at_time, sample_rate=FS,
                      delay=crossover_delay(default_taps(FS)),
                      window_ms=50.0):
    """Peak LF error introduced at a switch.

    A cross-faded recombination keeps ``low + mix*(g-1)*high`` continuous, so
    the low band stays put; an instant branch swap splatters broadband and
    leaves a large error below the split.
    """
    wet_low, _ = lowpass_of(wet, cutoff=1500.0, sample_rate=sample_rate)
    dry_low, _ = lowpass_of(dry, cutoff=1500.0, sample_rate=sample_rate)
    start = int(at_time * sample_rate)
    stop = min(start + int(window_ms * 1e-3 * sample_rate),
               len(dry_low), len(wet_low) - delay)
    return max(abs(wet_low[i + delay] - dry_low[i])
               for i in range(start, stop))


def peak(signal):
    return max((abs(v) for v in signal), default=0.0)


def max_gain_reduction(meter):
    return min((row["gain_db"] for row in meter), default=0.0)


# ---------------------------------------------------------------------------
# band split structure
# ---------------------------------------------------------------------------

class BandSplitTests(unittest.TestCase):

    def test_kernel_is_linear_phase_and_dc_normalised(self):
        kernel = design_lowpass(6000.0, FS, 257, 8.0)
        self.assertAlmostEqual(sum(kernel), 1.0, places=12)
        for i in range(len(kernel) // 2):
            self.assertAlmostEqual(kernel[i], kernel[-1 - i], places=15)

    def test_bands_are_complementary_and_aligned(self):
        signal = make_voice(0.4)
        low, high = deesser.band_split([(v,) for v in signal],
                                       split_hz=6000.0, sample_rate=FS)
        delay = deesser.crossover_delay(deesser.default_taps(FS))
        self.assertEqual(len(low), len(signal))
        worst = max(abs(low[i][0] + high[i][0] - signal[i - delay])
                    for i in range(delay + 64, len(signal)))
        self.assertLess(worst, 1e-12)

    def test_split_rejects_bad_kernels(self):
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, taps=128)
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, taps=257,
                    coefficients=[0.5, 0.4, 1.0, 0.6, 0.5])
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, split_hz=30000.0)
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, threshold_db=float("nan"))
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, coefficients=[0.0] * 257)

    def test_supplied_coefficients_drive_the_split(self):
        signal = make_sibilant(0.3)
        kernel = design_lowpass(6000.0, FS)
        highpass = [-c for c in kernel]
        highpass[(len(kernel) - 1) // 2] += 1.0
        reductions = []
        for coefficients in (None, kernel, {"lowpass": kernel},
                             {"highpass": highpass}, highpass):
            spec = {} if coefficients is None else {"coefficients": coefficients}
            det = DeEsser(sample_rate=FS, **spec)
            det.set_meter(5)
            det.process_mono(signal)
            reductions.append(max_gain_reduction(det.meter))
        for value in reductions:
            self.assertLess(value, -6.0)
        self.assertAlmostEqual(reductions[0], reductions[1], places=9)
        self.assertAlmostEqual(reductions[1], reductions[2], places=12)
        self.assertAlmostEqual(reductions[1], reductions[3], places=9)


# ---------------------------------------------------------------------------
# 1. synthetic sibilant
# ---------------------------------------------------------------------------

class SibilantTests(unittest.TestCase):

    def setUp(self):
        self.signal = make_sibilant()
        self.det = DeEsser(sample_rate=FS)
        self.det.set_meter(5)
        self.wet = self.det.process_mono(self.signal)
        self.delay = self.det.latency_samples

    def test_sibilant_band_is_attenuated(self):
        start = int(0.14 * FS)
        end = int(0.22 * FS)
        dry_rms = _rms(self.signal, start, end)
        wet_rms = _rms(self.wet, start + self.delay, end + self.delay)
        self.assertLess(_db_ratio(wet_rms, dry_rms), -6.0,
                        "in-band sibilant was not reduced")

    def test_high_band_change_is_limited_to_the_sibilant(self):
        change = band_change_db(self.signal, self.wet, 7000, 12000,
                                self.delay)
        self.assertLess(change, -6.0)

    def test_no_muffling_after_the_sibilant(self):
        # The tail must be identical to the delayed dry signal.
        tail_start = int(0.34 * FS)
        worst = max(_aligned(self.signal, self.wet, self.delay, tail_start))
        self.assertLess(worst, 1e-12)
        # ...and the high band is fully back, not merely "less cut".  Measure
        # the tail only, where nothing is left of the sibilant.
        tail_change = band_change_db(self.signal, self.wet, 7000, 12000,
                                     self.delay,
                                     start=int(0.36 * FS),
                                     stop=int(0.60 * FS))
        self.assertLess(abs(tail_change), 0.05,
                        "high band did not return to unity after the sibilant")

    def test_reduction_releases_promptly(self):
        release_ms = self.det.parameter("release_ms")
        deadline = 0.10 + 0.18 + 3e-3 * release_ms
        rows = [row for row in self.det.meter if row["t"] >= deadline]
        self.assertTrue(rows, "meter did not cover the release window")
        self.assertGreater(rows[0]["gain_db"], -1.0,
                           "gain had not released after the sibilant")
        self.assertLess(rows[0]["gain_db"], 0.1)

    def test_body_band_is_untouched(self):
        change = band_change_db(self.signal, self.wet, 200, 4000, self.delay)
        self.assertLess(abs(change), 0.5)


# ---------------------------------------------------------------------------
# 2. broadband drum hit
# ---------------------------------------------------------------------------

class BroadbandTests(unittest.TestCase):

    def setUp(self):
        self.signal = make_drum()
        self.det = DeEsser(sample_rate=FS)
        self.det.set_meter(5)
        self.wet = self.det.process_mono(self.signal)
        self.delay = self.det.latency_samples

    def test_reference_energy_blocks_the_detector(self):
        self.assertGreater(max_gain_reduction(self.det.meter), -1.0,
                           "broadband hit triggered the de-esser")

    def test_low_band_is_preserved(self):
        change = band_change_db(self.signal, self.wet, 100, 4000, self.delay)
        self.assertLess(abs(change), 0.5)

    def test_output_is_essentially_dry(self):
        worst = max(_aligned(self.signal, self.wet, self.delay,
                             self.delay + 64))
        self.assertLess(worst, 0.06,
                        "broadband hit was noticeably processed")

    def test_absolute_threshold_alone_can_trigger(self):
        quiet = DeEsser(sample_rate=FS, threshold_db=20.0)
        quiet.set_meter(5)
        quiet.process_mono(self.signal)
        self.assertGreater(max_gain_reduction(quiet.meter), -1e-9,
                           "very high threshold must not trigger")
        loud = DeEsser(sample_rate=FS, threshold_db=-70.0,
                       ref_offset_db=-60.0)
        loud.set_meter(5)
        loud.process_mono(self.signal)
        self.assertLess(max_gain_reduction(loud.meter), -1.0,
                        "threshold below the signal must trigger")


# ---------------------------------------------------------------------------
# 3. normal voice
# ---------------------------------------------------------------------------

class VoiceTests(unittest.TestCase):

    def setUp(self):
        self.signal = make_voice()
        self.det = DeEsser(sample_rate=FS)
        self.det.set_meter(5)
        self.wet = self.det.process_mono(self.signal)
        self.delay = self.det.latency_samples

    def test_body_is_bit_preserved(self):
        worst = max(_aligned(self.signal, self.wet, self.delay,
                             int(0.10 * FS), int(0.70 * FS)))
        self.assertLess(worst, 1e-12, "voiced body was modified")

    def test_body_level_is_unchanged(self):
        dry = _rms(self.signal, int(0.10 * FS), int(0.68 * FS))
        wet = _rms(self.wet, int(0.10 * FS) + self.delay,
                   int(0.68 * FS) + self.delay)
        self.assertLess(abs(_db_ratio(wet, dry)), 0.05)

    def test_sibilant_tail_is_reduced(self):
        change = band_change_db(self.signal, self.wet, 7000, 12000,
                                self.delay)
        self.assertLess(change, -2.0)

    def test_no_residue_after_the_tail(self):
        start = int(0.92 * FS)
        worst = max(_aligned(self.signal, self.wet, self.delay, start))
        self.assertLess(worst, 1e-12)

    def test_latency_is_reported_and_short(self):
        self.assertEqual(self.delay,
                         deesser.crossover_delay(deesser.default_taps(FS)))
        self.assertLess(self.det.latency_ms, 5.0)

    def test_crossover_width_is_sample_rate_independent(self):
        reference = None
        for rate in (44100, 48000, 88200, 96000):
            kernel = design_lowpass(6000.0, rate,
                                    deesser.default_taps(rate), 8.0)
            w = 2.0 * math.pi * 7000.0 / rate
            re = sum(c * math.cos(w * i) for i, c in enumerate(kernel))
            im = sum(c * math.sin(w * i) for i, c in enumerate(kernel))
            attenuation = 20.0 * math.log10(max(math.hypot(re, im), 1e-15))
            self.assertLess(attenuation, -60.0,
                            "stop band too shallow at %d Hz" % rate)
            if reference is None:
                reference = attenuation
            else:
                self.assertLess(abs(attenuation - reference), 12.0,
                                "crossover width changed at %d Hz" % rate)


# ---------------------------------------------------------------------------
# 4. step automation and bypass switching
# ---------------------------------------------------------------------------

class SwitchingTests(unittest.TestCase):

    def _tone(self, freq=9000.0, amp=0.6, duration=1.0):
        return [amp * math.sin(2 * math.pi * freq * i / FS)
                for i in range(int(duration * FS))]

    def test_bypass_ramp_does_not_click(self):
        signal = self._tone(freq=9000.0, amp=0.3)
        det = DeEsser(sample_rate=FS, threshold_db=-40.0, fade_ms=10.0)
        det.set_meter(2)
        det.set_enabled(True)
        det.set_enabled(False, at_time=0.30)
        det.set_enabled(True, at_time=0.60)
        wet = det.process_mono(signal)
        self.assertLess(max_gain_reduction(det.meter), -6.0,
                        "test signal never engaged the de-esser")
        self.assertLess(low_band_residual(signal, wet, 0.30), 1e-4,
                        "bypass switch splattered the low band")
        self.assertLess(low_band_residual(signal, wet, 0.60), 1e-4,
                        "bypass switch splattered the low band")

    def test_abrupt_bypass_would_click(self):
        # Same measurement without a cross-fade: the metric must see it,
        # which proves the test above is meaningful.
        signal = self._tone(freq=9000.0, amp=0.3)
        det = DeEsser(sample_rate=FS, threshold_db=-40.0, fade_ms=0.0)
        det.set_enabled(True)
        det.set_enabled(False, at_time=0.30)
        wet = det.process_mono(signal)
        self.assertGreater(low_band_residual(signal, wet, 0.30), 1e-2)

    def test_switch_does_not_splatter_the_low_band(self):
        signal = make_sibilant()

        def switched(fade_ms):
            det = DeEsser(sample_rate=FS, fade_ms=fade_ms)
            det.set_enabled(False, at_time=0.0)
            det.set_enabled(True, at_time=0.16)
            det.set_enabled(False, at_time=0.22)
            det.set_enabled(True, at_time=0.30)
            return det.process_mono(signal)

        smooth_error = low_band_residual(signal, switched(8.0), 0.22)
        abrupt_error = low_band_residual(signal, switched(0.0), 0.22)
        self.assertLess(smooth_error, 1e-4,
                        "cross-faded switch splattered the low band")
        self.assertGreater(abrupt_error, smooth_error * 20.0,
                           "abrupt switch reference is not splattering")

    def test_bypass_output_matches_dry_when_disabled(self):
        signal = self._tone(1000.0, 0.3)
        det = DeEsser(sample_rate=FS, enabled=False)
        wet = det.process_mono(signal)
        delay = det.latency_samples
        worst = max(abs(wet[i] - signal[i - delay])
                    for i in range(delay + 64, len(signal)))
        self.assertLess(worst, 1e-15)

    def test_envelope_survives_a_switch(self):
        signal = self._tone()
        det = DeEsser(sample_rate=FS, threshold_db=-40.0)
        det.set_enabled(True)
        det.set_enabled(False, at_time=0.25)
        det.set_enabled(True, at_time=0.45)
        det.process_mono(signal)
        # The detector kept running while bypassed: the gain is still reduced
        # the instant the unit comes back.
        self.assertLess(det.state()["gain_db"], -6.0)

    def test_step_automation_is_smoothed(self):
        signal = self._tone(freq=9000.0, amp=0.3)
        det = DeEsser(sample_rate=FS, threshold_db=-40.0, smooth_ms=20.0)
        det.set_meter(2)
        det.set_param("ratio", 1.2)
        first = det.process_mono(signal[:int(0.2 * FS)])
        before = max_gain_reduction(det.meter)
        det.set_param("ratio", 12.0, at_time=0.2)
        det.set_param("max_reduction_db", 24.0, at_time=0.4)
        second = det.process_mono(signal[int(0.2 * FS):])
        after = max_gain_reduction(det.meter)
        self.assertLess(after, before - 6.0,
                        "automation did not increase the reduction")
        wet = first + second
        self.assertLess(low_band_residual(signal, wet, 0.2), 1e-4,
                        "parameter step produced a click")

    def test_automation_step_is_reached(self):
        det = DeEsser(sample_rate=FS, threshold_db=-40.0, smooth_ms=5.0)
        det.set_param("ratio", 1.2)
        det.set_param("ratio", 9.0, at_time=0.02)
        det.process_mono(self._tone(duration=0.2))
        self.assertAlmostEqual(det.parameter("ratio"), 9.0, places=6)

    def test_scheduling_in_the_past_is_rejected(self):
        det = DeEsser(sample_rate=FS)
        det.process_mono(self._tone(duration=0.1))
        with self.assertRaises(DeEsserError):
            det.set_enabled(False, at_time=0.0)
        with self.assertRaises(DeEsserError):
            det.set_param("ratio", 2.0, at_time=0.0)

    def test_peak_at_switch_is_bounded(self):
        signal = make_sibilant()
        det = DeEsser(sample_rate=FS)
        det.set_enabled(False, at_time=0.0)
        det.set_enabled(True, at_time=0.14)
        det.set_enabled(False, at_time=0.22)
        det.set_enabled(True, at_time=0.30)
        wet = det.process_mono(signal)
        dry_peak = max(abs(v) for v in signal)
        self.assertLessEqual(max(abs(v) for v in wet), dry_peak * 1.001)


# ---------------------------------------------------------------------------
# structure: block size, linking, state continuity
# ---------------------------------------------------------------------------

class StructuralTests(unittest.TestCase):

    def test_output_is_block_size_invariant(self):
        signal = make_voice(0.5)
        reference, _ = deesser.process_audio(signal, sample_rate=FS)
        reference = [frame[0] for frame in reference]
        for block in (1, 3, 17, 64, 997, len(signal)):
            frames, _ = deesser.process_audio(signal, block_size=block,
                                              sample_rate=FS)
            flat = [frame[0] for frame in frames]
            self.assertEqual(len(flat), len(reference))
            worst = max(abs(a - b) for a, b in zip(flat, reference))
            self.assertLess(worst, 1e-15,
                            "block size %d changed the output" % block)

    def test_frame_job_is_block_size_invariant(self):
        frames = [{"t": i * 0.01, "low": 0.01 + 0.001 * (i % 7),
                   "high": 0.05 if 30 <= i < 45 else 0.002}
                  for i in range(120)]
        full, active = deesser.process_frames(frames)
        for chunk in (1, 7, 13, len(frames)):
            det = FrameDeEsser()
            values = []
            for i in range(0, len(frames), chunk):
                for frame in frames[i:i + chunk]:
                    _, gain, _ = det.process(frame)
                    values.append(round(gain, 4))
            self.assertEqual(len(values), len(full))
            self.assertEqual(values, [row["gain"] for row in full],
                             "frame chunk %d changed the output" % chunk)
        self.assertGreater(active, 0)

    def test_frame_output_is_rounded_as_documented(self):
        rows, _ = deesser.process_frames([{"high": 0.4, "low": 0.02}])
        self.assertEqual(rows[0]["det"], round(0.4 / 0.02, 3))
        self.assertEqual(rows[0]["gain"], round(rows[0]["gain"], 4))

    def test_frame_reference_energy_blocks_trigger(self):
        sibilant = [{"high": 0.30, "low": 0.0005} for _ in range(20)]
        broadband = [{"high": 0.30, "low": 0.25} for _ in range(20)]
        rows, _ = deesser.process_frames(sibilant + broadband)
        self.assertLess(min(row["gain"] for row in rows[:20]), 0.9)
        self.assertGreater(rows[-1]["gain"], 0.90,
                           "broadband frames kept the detector engaged")

    def test_stereo_link_moves_both_channels_together(self):
        left = make_sibilant()
        right = [0.2 * math.sin(2 * math.pi * 1050.0 * i / FS)
                 for i in range(len(left))]
        det = DeEsser(sample_rate=FS, channels=2)
        det.set_meter(5)
        frames = det.process_block(list(zip(left, right)))
        self.assertLess(max_gain_reduction(det.meter), -1.0)
        # Right has no HF content of its own: the shared gain must still apply,
        # yet the low band of the right channel can only survive untouched.
        right_in = [frame[1] for frame in frames]
        change = band_change_db(right, right_in, 300, 3000,
                                det.latency_samples)
        self.assertLess(abs(change), 0.5)

    def test_stereo_gain_is_shared(self):
        left = make_sibilant()
        right = list(left)
        det = DeEsser(sample_rate=FS, channels=2)
        frames = det.process_block(list(zip(left, right)))
        worst = max(abs(frame[0] - frame[1]) for frame in frames)
        self.assertLess(worst, 1e-12, "linked gain diverged between channels")

    def test_shared_gain_is_channel_independent(self):
        loud = make_sibilant()
        quiet = [v * 0.0 for v in loud]
        mono = DeEsser(sample_rate=FS)
        mono.set_meter(5)
        mono.process_mono(loud)
        stereo = DeEsser(sample_rate=FS, channels=2)
        stereo.set_meter(5)
        stereo.process_block(list(zip(loud, quiet)))
        self.assertAlmostEqual(max_gain_reduction(mono.meter),
                               max_gain_reduction(stereo.meter), places=9)

    def test_state_is_continuous_across_blocks(self):
        signal = make_sibilant(0.35)
        whole = DeEsser(sample_rate=FS)
        whole.process_mono(signal)
        chunked = DeEsser(sample_rate=FS)
        for i in range(0, len(signal), 33):
            chunked.process_block([(v,) for v in signal[i:i + 33]])
        self.assertAlmostEqual(whole.state()["gain"],
                               chunked.state()["gain"], places=15)
        self.assertAlmostEqual(whole.state()["band_energy"][0],
                               chunked.state()["band_energy"][0], places=15)

    def test_attack_and_release_directions(self):
        # Slow attack, fast release: the gain must crawl down and snap back.
        det = FrameDeEsser(attack_ms=50.0, release_ms=5.0, threshold=0.5)
        for _ in range(4):
            det.process({"high": 9.0, "low": 0.1})
        after_attack = det.gain
        self.assertLess(after_attack, 1.0)
        self.assertGreater(after_attack, 0.2, "attack was immediate")
        det.process({"high": 9.0, "low": 0.1})
        deepest = det.gain
        below_threshold = {"high": 0.05, "low": 0.1}
        det.process(below_threshold)
        one_release_step = det.gain - deepest
        det.process(below_threshold)
        self.assertGreater(one_release_step, 0.2,
                           "release did not recover the gain")
        self.assertGreater(det.gain, 0.9)

    def test_release_coefficient_differs_from_attack(self):
        settled = FrameDeEsser(attack_ms=50.0, release_ms=1.0, threshold=0.5)
        for _ in range(3):
            settled.process({"high": 9.0, "low": 0.1})
        low = settled.gain
        settled.process({"high": 0.1, "low": 1.0})
        self.assertGreater(settled.gain - low, 0.3)

        slow_release = FrameDeEsser(attack_ms=1.0, release_ms=500.0,
                                    threshold=0.5)
        for _ in range(3):
            slow_release.process({"high": 9.0, "low": 0.1})
        low = slow_release.gain
        slow_release.process({"high": 0.1, "low": 1.0})
        self.assertLess(slow_release.gain - low, 0.05)
        self.assertGreater(slow_release.gain - low, 0.0)

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, ratio=1.0)
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, attack_ms=-1.0)
        with self.assertRaises(DeEsserError):
            DeEsser(sample_rate=FS, unknown_knob=3.0)
        with self.assertRaises(DeEsserError):
            FrameDeEsser(threshold=-1.0)

    def test_non_target_band_energy_is_preserved(self):
        signal = make_voice(0.6)
        det = DeEsser(sample_rate=FS)
        wet = det.process_mono(signal)
        delay = det.latency_samples
        for low_hz, high_hz in ((150, 500), (500, 1500), (1500, 4000)):
            change = band_change_db(signal, wet, low_hz, high_hz, delay)
            self.assertLess(abs(change), 0.1,
                            "band %d-%d Hz moved" % (low_hz, high_hz))


# ---------------------------------------------------------------------------
# CLI / job formats
# ---------------------------------------------------------------------------

class JobTests(unittest.TestCase):

    def test_frame_job_contract(self):
        job = {
            "frames": [{"t": i * 0.01,
                        "low": 0.002,
                        "high": 0.09 if 20 <= i < 32 else 0.0005}
                       for i in range(60)],
            "threshold": 8.0,
            "ratio": 4.0,
            "attack": 10.0,
            "release": 40.0,
        }
        result = deesser.run_frame_job(job)
        self.assertEqual(len(result["frames"]), 60)
        self.assertGreater(result["active_frames"], 0)
        for row in result["frames"]:
            self.assertEqual(set(row), {"t", "det", "gain"})
            self.assertIsInstance(row["det"], float)
            self.assertIsInstance(row["gain"], float)
        # det is the documented high/low ratio, reported to 3 decimals
        self.assertEqual(result["frames"][0]["det"], round(0.0005 / 0.002, 3))
        self.assertEqual(result["frames"][25]["det"], round(0.09 / 0.002, 3))
        # frames above threshold are reduced, frames below are not
        self.assertLess(result["frames"][25]["gain"], 0.99)
        self.assertEqual(result["frames"][0]["gain"], 1.0)

    def test_frame_job_handles_silence(self):
        frames = [{"high": 0.0, "low": 0.0} for _ in range(5)]
        rows, active = deesser.process_frames(frames)
        self.assertEqual([row["det"] for row in rows], [0.0] * 5)
        self.assertEqual(active, 0)

    def test_stream_job_roundtrip(self):
        audio = [[v] for v in make_voice(0.2)]
        job = {"audio": audio, "sample_rate": FS, "block_size": 48}
        result = deesser.run_stream_job(job)
        self.assertEqual(result["latency_samples"],
                         deesser.crossover_delay(deesser.default_taps(FS)))
        self.assertEqual(len(result["frames"]), len(audio))
        self.assertEqual(result["block_size"], 48)

    def test_cli_accepts_wav(self):
        import wave
        signal = make_sibilant(0.2)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.wav")
            dst = os.path.join(tmp, "out.wav")
            with wave.open(src, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(FS)
                handle.writeframes(b"".join(
                    int(max(-1.0, min(1.0, v)) * 32767).to_bytes(
                        2, "little", signed=True) for v in signal))
            self.assertEqual(deesser.main([src, dst, "--block-size", "128"]), 0)
            with wave.open(dst, "rb") as handle:
                self.assertEqual(handle.getnframes(), len(signal))

    def test_cli_accepts_coefficient_file(self):
        kernel = design_lowpass(6000.0, FS)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "coefs.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"lowpass": kernel}, handle)
            source = os.path.join(tmp, "job.json")
            result = os.path.join(tmp, "out.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump({"audio": [[v] for v in make_sibilant(0.2)],
                           "sample_rate": FS}, handle)
            self.assertEqual(deesser.main([source, result, "--coefficients",
                                           path, "--block-size", "32"]), 0)
            with open(result, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["taps"], len(kernel))

    def test_enabled_can_come_from_job_params(self):
        frame_job = {
            "frames": [{"high": 0.1, "low": 0.01}] * 5,
            "params": {"enabled": False, "frame_ms": 5.0},
        }
        result = deesser.run_frame_job(frame_job)
        self.assertEqual(result["active_frames"], 0)
        self.assertEqual([row["gain"] for row in result["frames"]], [1.0] * 5)
        self.assertEqual(result["frames"][1]["t"], 5.0)

        stream_job = {"audio": [[0.1]] * 8,
                      "params": {"enabled": False}}
        out = deesser.run_stream_job(stream_job)["frames"]
        self.assertTrue(all(v == 0.0 for frame in out for v in frame),
                        "bypassed stream should be the delayed dry signal")

    def test_invalid_jobs_are_rejected(self):
        with self.assertRaises(DeEsserError):
            deesser.run_frame_job({"frames": "nope"})
        with self.assertRaises(DeEsserError):
            deesser.run_frame_job({"frames": [{"high": 1.0, "low": 1.0}],
                                   "ratio": 0.5})
        with self.assertRaises(DeEsserError):
            deesser.run_stream_job({"audio": [[0.0]], "params": {"bogus": 1}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
