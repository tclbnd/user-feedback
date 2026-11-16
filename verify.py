#!/usr/bin/env python3
"""Acceptance report for the de-esser.

Runs the four required scenarios and prints the numbers used for sign-off:

* synthetic sibilant      - band attenuation, release, no residue
* broadband drum hit      - reference energy veto, LF preserved
* normal voice            - body bit-exact, sibilant reduced
* abrupt automation       - parameter steps and bypass switches stay click-free

Also checks block-size invariance and the complementary band split.

Usage::

    python3 verify.py            # human readable
    python3 verify.py --json     # machine readable
"""

import argparse
import json
import math
import sys

import deesser
from deesser import DeEsser

FS = 48000
TAPS = deesser.default_taps(FS)
DELAY = (TAPS - 1) // 2


# -- signal generators ------------------------------------------------------

def make_sibilant(duration=0.6, start=0.10, length=0.18, fs=FS, seed=11):
    import random
    rnd = random.Random(seed)
    n = int(duration * fs)
    out = []
    smoothed = 0.0
    for i in range(n):
        t = i / fs
        env = (math.sin(math.pi * (t - start) / length) ** 2
               if start <= t < start + length else 0.0)
        noise = rnd.uniform(-1.0, 1.0)
        smoothed += (noise - smoothed) * 0.35
        out.append(env * (0.40 * math.sin(2 * math.pi * 8200 * t)
                          + 0.22 * (noise - smoothed)
                          + 0.12 * math.sin(2 * math.pi * 6800 * t)))
    return out


def make_drum(duration=0.5, fs=FS, seed=3):
    import random
    rnd = random.Random(seed)
    n = int(duration * fs)
    out = []
    smoothed = 0.0
    for i in range(n):
        t = i / fs
        env = math.exp(-t / 0.10)
        noise = rnd.uniform(-1.0, 1.0)
        smoothed += (noise - smoothed) * 0.12
        body = (0.55 * math.sin(2 * math.pi * 62 * t)
                + 0.30 * math.sin(2 * math.pi * 175 * t))
        out.append(env * (0.45 * noise + 0.55 * body))
    return out


def make_voice(duration=1.0, fs=FS, seed=5):
    import random
    rnd = random.Random(seed)
    n = int(duration * fs)
    out = []
    for i in range(n):
        t = i / fs
        env = 0.5 * (1.0 - math.cos(2 * math.pi
                                   * min(1.0, max(0.0, (t - 0.05) / 0.55))))
        if t > 0.70:
            env *= max(0.0, 1.0 - (t - 0.70) / 0.18)
        f0 = 118.0 + 14.0 * math.sin(2 * math.pi * 4.0 * t)
        glottal = math.sin(2 * math.pi * f0 * t) * (
            1.0 + 0.25 * math.sin(2 * math.pi * f0 * 2.0 * t))
        body = (0.50 * math.sin(2 * math.pi * 680.0 * t)
                + 0.32 * math.sin(2 * math.pi * 1180.0 * t)
                + 0.18 * math.sin(2 * math.pi * 2350.0 * t))
        tail = 0.0
        if 0.74 <= t < 0.88:
            e = math.sin(math.pi * (t - 0.74) / 0.14) ** 2
            tail = e * (0.30 * math.sin(2 * math.pi * 8000.0 * t)
                        + 0.20 * rnd.uniform(-1.0, 1.0))
        out.append(env * glottal * body + tail)
    return out


# -- measurement helpers ----------------------------------------------------

def rms(values, start=0, stop=None):
    stop = len(values) if stop is None else stop
    chunk = values[start:stop]
    if not chunk:
        return 0.0
    return math.sqrt(sum(v * v for v in chunk) / len(chunk))


def band_power(signal, low, high, fs=FS, step=250):
    total = 0.0
    n = len(signal)
    for freq in range(int(low), int(high) + 1, step):
        w = 2.0 * math.pi * freq / fs
        re = 0.0
        im = 0.0
        for i, value in enumerate(signal):
            re += value * math.cos(w * i)
            im -= value * math.sin(w * i)
        total += (re * re + im * im) / (n * n)
    return total


def band_change_db(dry, wet, low, high, delay=DELAY, fs=FS):
    d = band_power(dry, low, high, fs)
    w = band_power(wet[delay:], low, high, fs)
    return 10.0 * math.log10(max(w, 1e-30) / max(d, 1e-30))


def aligned_error(dry, wet, delay=DELAY, start=0, stop=None):
    stop = min(len(dry) if stop is None else stop, len(wet) - delay)
    return max((abs(wet[i + delay] - dry[i]) for i in range(start, stop)),
               default=0.0)


def low_band_residual(dry, wet, at_time, fs=FS, delay=DELAY, window_ms=50.0):
    low_dry, _ = deesser.band_split([(v,) for v in dry], split_hz=1500.0,
                                    sample_rate=fs)
    low_wet, _ = deesser.band_split([(v,) for v in wet], split_hz=1500.0,
                                    sample_rate=fs)
    start = int(at_time * fs)
    stop = min(start + int(window_ms * 1e-3 * fs), len(low_dry),
               len(low_wet) - delay)
    return max((abs(low_wet[i + delay][0] - low_dry[i][0])
                for i in range(start, stop)), default=0.0)


def max_reduction(meter):
    return min((row["gain_db"] for row in meter), default=0.0)


# -- scenarios --------------------------------------------------------------

def scenario_sibilant():
    signal = make_sibilant()
    det = DeEsser(sample_rate=FS)
    det.set_meter(5)
    wet = det.process_mono(signal)
    tail_start = int(0.34 * FS)
    return {
        "latency_samples": det.latency_samples,
        "latency_ms": round(det.latency_ms, 3),
        "sibilant_band_change_db": round(
            band_change_db(signal, wet, 7000, 12000), 2),
        "sibilant_rms_change_db": round(20.0 * math.log10(
            max(rms(wet, int(0.14 * FS) + DELAY, int(0.22 * FS) + DELAY),
                1e-12) / max(rms(signal, int(0.14 * FS), int(0.22 * FS)),
                             1e-12)), 2),
        "max_reduction_db": round(max_reduction(det.meter), 2),
        "tail_max_error": round(aligned_error(signal, wet, start=tail_start),
                                15),
        "tail_gain_db": round(det.meter[-1]["gain_db"], 2),
        "body_band_change_db": round(
            band_change_db(signal, wet, 200, 4000), 3),
    }


def scenario_drum():
    signal = make_drum()
    det = DeEsser(sample_rate=FS)
    det.set_meter(5)
    wet = det.process_mono(signal)
    return {
        "max_reduction_db": round(max_reduction(det.meter), 3),
        "low_band_change_db": round(
            band_change_db(signal, wet, 100, 4000), 3),
        "high_band_change_db": round(
            band_change_db(signal, wet, 7000, 16000), 3),
        "max_error_vs_dry": round(aligned_error(signal, wet, start=DELAY + 64),
                                  6),
    }


def scenario_voice():
    signal = make_voice()
    det = DeEsser(sample_rate=FS)
    det.set_meter(5)
    wet = det.process_mono(signal)
    return {
        "body_max_error": round(aligned_error(signal, wet, start=int(0.10 * FS),
                                              stop=int(0.70 * FS)), 15),
        "body_rms_change_db": round(20.0 * math.log10(
            max(rms(wet, int(0.10 * FS) + DELAY, int(0.68 * FS) + DELAY), 1e-12)
            / max(rms(signal, int(0.10 * FS), int(0.68 * FS)), 1e-12)), 3),
        "sibilant_band_change_db": round(
            band_change_db(signal, wet, 7000, 12000), 2),
        "max_reduction_db": round(max_reduction(det.meter), 2),
        "residue_after_tail": round(aligned_error(signal, wet,
                                                  start=int(0.92 * FS)), 15),
        "body_bands_change_db": [
            round(band_change_db(signal, wet, lo, hi), 3)
            for lo, hi in ((150, 500), (500, 1500), (1500, 4000))
        ],
    }


def scenario_automation():
    signal = make_sibilant()

    def switched(fade_ms, events):
        det = DeEsser(sample_rate=FS, fade_ms=fade_ms)
        for time, enabled in events:
            det.set_enabled(enabled, at_time=time)
        return det, det.process_mono(signal)

    events = [(0.0, False), (0.16, True), (0.22, False), (0.30, True)]
    _, smooth = switched(8.0, events)
    _, abrupt = switched(0.0, events)

    # Parameter automation: ratio 1.2 -> 12 and a higher ceiling, stepped at
    # 0.2 s and 0.4 s on a steady HF tone.
    tone = [0.3 * math.sin(2 * math.pi * 9000.0 * i / FS)
            for i in range(int(0.6 * FS))]
    det = DeEsser(sample_rate=FS, threshold_db=-40.0, smooth_ms=20.0,
                  max_reduction_db=6.0)
    det.set_meter(2)
    det.set_param("ratio", 1.2)
    head = det.process_mono(tone[:int(0.2 * FS)])
    before = det.meter[-1]["gain_db"]
    det.set_param("ratio", 12.0, at_time=0.2)
    det.set_param("max_reduction_db", 18.0, at_time=0.4)
    tail = det.process_mono(tone[int(0.2 * FS):])
    after = det.meter[-1]["gain_db"]
    wet_tone = head + tail

    # State continuity across a switch: stay bypassed through the whole
    # sibilant, then inspect the envelope the detector kept tracking.
    continuity = DeEsser(sample_rate=FS, enabled=False)
    continuity.process_mono(signal[:int(0.24 * FS)])

    return {
        "bypass_low_band_residual": round(low_band_residual(signal, smooth,
                                                           0.22), 8),
        "abrupt_low_band_residual": round(low_band_residual(signal, abrupt,
                                                           0.22), 6),
        "automation_reduction_before_db": round(before, 2),
        "automation_reduction_after_db": round(after, 2),
        "automation_low_band_residual": round(
            low_band_residual(tone, wet_tone, 0.2), 8),
        "gain_after_bypass_interval_db": round(
            continuity.state()["gain_db"], 2),
    }


def scenario_structure():
    signal = make_voice(0.5)
    reference, _ = deesser.process_audio(signal, sample_rate=FS)
    reference = [frame[0] for frame in reference]
    worst = 0.0
    for block in (1, 3, 17, 64, 997, len(signal)):
        frames, _ = deesser.process_audio(signal, block_size=block,
                                          sample_rate=FS)
        flat = [frame[0] for frame in frames]
        worst = max(worst, max(abs(a - b) for a, b in zip(flat, reference)))

    low, high = deesser.band_split([(v,) for v in signal], split_hz=6000.0,
                                   sample_rate=FS)
    complement = max((abs(low[i][0] + high[i][0] - signal[i - DELAY])
                      for i in range(DELAY + 64, len(signal))), default=0.0)
    return {
        "block_invariance_max_diff": round(worst, 15),
        "split_complementarity_max_error": round(complement, 15),
        "delay_samples": DELAY,
    }


def collect():
    return {
        "sibilant": scenario_sibilant(),
        "broadband_drum": scenario_drum(),
        "normal_voice": scenario_voice(),
        "automation": scenario_automation(),
        "structure": scenario_structure(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON")
    args = parser.parse_args(argv)
    report = collect()
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    for name, values in report.items():
        print(name)
        for key, value in values.items():
            print("  %-32s %s" % (key, value))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
