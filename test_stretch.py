"""Acceptance tests for stretch.py (run: python3 -m unittest test_stretch -v).

Covers:
  1. Pitch preservation on a sine (independent zero-crossing detector, <= 1%)
     and impulse mapping on known drum hits (independent peak detector,
     p95 <= 15 ms); output length error <= 1 sample.
  2. Stereo relation: exact gain + polarity input keeps correlation <= -0.995;
     random blockings give identical length and peak positions.
  3. Edge cases: short files, silence, speed extremes, exported fields.
"""
import math
import random
import unittest
from bisect import bisect_left

import stretch

RATE = 8000
RAMP_KNOTS = [[0, 0.7], [6000, 1.6]]  # smooth 0.7 -> 1.6 transition


# ------------------------------------------------------------- helpers
def clamp_speed(s):
    return min(1.6, max(0.7, s))


def independent_speed(knots):
    pts = sorted((int(t), clamp_speed(s)) for t, s in knots)

    def speed(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            if t < pts[i][0]:
                t0, s0 = pts[i - 1]
                t1, s1 = pts[i]
                return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
        return pts[-1][1]

    return speed


def independent_length(knots, n_in):
    """Trapezoid integration of the piecewise-linear speed curve."""
    if n_in <= 0:
        return 0
    speed = independent_speed(knots)
    cum = 0.0
    t = 0
    while cum < n_in:
        cum += 0.5 * (speed(t) + speed(t + 1))
        t += 1
    return t


def render(frames, knots):
    return stretch.render({"rate": RATE, "blocks": [frames], "knots": knots})


def sine(freq, n, amp=0.8):
    return [[amp * math.sin(2.0 * math.pi * freq * i / RATE), 0.0] for i in range(n)]


def measure_freq(x, rate=RATE):
    """Independent frequency estimate: positive zero-crossing intervals."""
    cross = [i for i in range(1, len(x)) if x[i - 1] < 0.0 <= x[i]]
    if len(cross) < 3:
        return 0.0
    return (len(cross) - 1) * rate / (cross[-1] - cross[0])


def find_peaks(x, thresh, gap):
    """Independent impulse detector: local |x| maxima above thresh."""
    peaks = []
    best = -1
    bestv = 0.0
    cool = 0
    for i, v in enumerate(x):
        a = abs(v)
        if a > thresh:
            if a > bestv:
                bestv = a
                best = i
            cool = gap
        elif cool > 0:
            cool -= 1
            if cool == 0 and best >= 0:
                peaks.append(best)
                best = -1
                bestv = 0.0
    if best >= 0:
        peaks.append(best)
    return peaks


def pearson(a, b):
    n = len(a)
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def p95(values):
    s = sorted(values)
    return s[int(0.95 * (len(s) - 1))]


# ---------------------------------------------------------------- tests
class TestPitchAndImpulses(unittest.TestCase):
    def test_pitch_preserved_on_sine(self):
        frames = sine(440.0, 8000)
        res = render(frames, RAMP_KNOTS)
        out = [f[0] for f in res["frames"]]
        t = len(out)
        mid = out[t // 3 : 2 * t // 3]
        f0 = measure_freq(mid)
        self.assertLessEqual(abs(f0 - 440.0) / 440.0, 0.01)

    def test_impulse_mapping(self):
        n_in = 12000
        frames = [[0.0, 0.0] for _ in range(n_in)]
        clicks = [2400, 4800, 7200, 9600]
        for p in clicks:
            for k in range(40):
                frames[p + k][0] += (
                    0.9 * math.sin(2.0 * math.pi * 1000.0 * k / RATE) * math.exp(-k / 10.0)
                )
        res = render(frames, RAMP_KNOTS)
        out = [f[0] for f in res["frames"]]
        mapping = res["mapping"]

        thresh = 0.15 * max(abs(v) for v in out)
        peaks = find_peaks(out, thresh, gap=400)
        expected = [bisect_left(mapping, p) for p in clicks]
        self.assertEqual(len(peaks), len(expected))
        errors = [abs(p - e) for p, e in zip(peaks, expected)]
        self.assertLessEqual(p95(errors), 0.015 * RATE)  # p95 <= 15 ms
        # exported transient list must react to the drum hits
        self.assertGreaterEqual(len(res["transients"]["input"]), len(clicks))

    def test_output_length_within_one_sample(self):
        cases = [
            (5, [[0, 0.7], [5, 1.6]]),
            (1, [[0, 0.7]]),
            (100, [[0, 1.6]]),
            (3000, [[0, 0.7]]),
            (8000, RAMP_KNOTS),
            (10000, [[0, 0.7], [3000, 1.2], [7000, 1.6]]),
        ]
        for n_in, knots in cases:
            frames = [[0.1 * math.sin(i), -0.1 * math.sin(i)] for i in range(n_in)]
            res = render(frames, knots)
            expected = independent_length(knots, n_in)
            self.assertLessEqual(
                abs(len(res["frames"]) - expected), 1, f"n_in={n_in} knots={knots}"
            )
            self.assertEqual(len(res["mapping"]), len(res["frames"]))


class TestStereoAndBlocking(unittest.TestCase):
    def test_exact_gain_and_polarity_relation(self):
        n_in = 8000
        frames = []
        for i in range(n_in):
            l = 0.6 * math.sin(2.0 * math.pi * 300.0 * i / RATE) + 0.4 * math.sin(
                2.0 * math.pi * 520.0 * i / RATE
            )
            frames.append([l, -0.83 * l])
        res = render(frames, RAMP_KNOTS)
        t = len(res["frames"])
        lo, hi = t // 5, 4 * t // 5
        left = [f[0] for f in res["frames"][lo:hi]]
        right = [f[1] for f in res["frames"][lo:hi]]
        self.assertLessEqual(pearson(left, right), -0.995)

    def test_random_blocking_invariance(self):
        n_in = 6000
        frames = []
        for i in range(n_in):
            v = 0.5 * math.sin(2.0 * math.pi * 440.0 * i / RATE)
            frames.append([v, -0.7 * v])
        for p in (1500, 3000, 4500):
            for k in range(30):
                frames[p + k][0] += 0.8 * math.exp(-k / 8.0)
                frames[p + k][1] -= 0.56 * math.exp(-k / 8.0)
        base = render(frames, RAMP_KNOTS)
        base_peaks = find_peaks([f[0] for f in base["frames"]], 0.2, gap=300)

        rng = random.Random(20240613)
        for _ in range(3):
            blocks = []
            i = 0
            while i < n_in:
                step = rng.randint(1, 997)
                blocks.append(frames[i : i + step])
                i += step
            res = stretch.render({"rate": RATE, "blocks": blocks, "knots": RAMP_KNOTS})
            self.assertEqual(len(res["frames"]), len(base["frames"]))
            peaks = find_peaks([f[0] for f in res["frames"]], 0.2, gap=300)
            self.assertEqual(peaks, base_peaks)
            self.assertEqual(res["frames"], base["frames"])


class TestEdgesAndExports(unittest.TestCase):
    def test_silence_stays_finite_and_silent(self):
        res = render([[0.0, 0.0]] * 6000, RAMP_KNOTS)
        self.assertLessEqual(abs(len(res["frames"]) - independent_length(RAMP_KNOTS, 6000)), 1)
        peak = max((abs(v) for f in res["frames"] for v in f), default=0.0)
        self.assertEqual(peak, 0.0)
        self.assertEqual(res["transients"], {"input": [], "output": []})

    def test_empty_input(self):
        res = stretch.render({"rate": RATE, "blocks": [[]], "knots": RAMP_KNOTS})
        self.assertEqual(res["frames"], [])
        self.assertEqual(res["mapping"], [])

    def test_short_file(self):
        res = render([[0.5, -0.5]] * 10, [[0, 0.7], [4, 1.6]])
        self.assertLessEqual(abs(len(res["frames"]) - independent_length([[0, 0.7], [4, 1.6]], 10)), 1)

    def test_speed_extremes(self):
        for knots in ([[0, 0.7]], [[0, 1.6]]):
            frames = sine(440.0, 6000)
            res = render(frames, knots)
            self.assertLessEqual(abs(len(res["frames"]) - independent_length(knots, 6000)), 1)
            out = [f[0] for f in res["frames"]]
            t = len(out)
            f0 = measure_freq(out[t // 3 : 2 * t // 3])
            self.assertLessEqual(abs(f0 - 440.0) / 440.0, 0.01)

    def test_speed_clamped_to_spec_range(self):
        sm = stretch.SpeedMap([[0, 0.5], [100, 3.0]])
        self.assertEqual(sm.speed(0), 0.7)
        self.assertEqual(sm.speed(200), 1.6)

    def test_exported_fields(self):
        res = render(sine(440.0, 4000), RAMP_KNOTS)
        mapping = res["mapping"]
        self.assertEqual(mapping[0], 0.0)
        self.assertTrue(all(b >= a for a, b in zip(mapping, mapping[1:])))
        self.assertEqual(res["latency"], stretch.NFFT)
        self.assertIn("input", res["transients"])
        self.assertIn("output", res["transients"])
        self.assertEqual(
            sorted(res["transients"]["input"]), res["transients"]["input"]
        )


if __name__ == "__main__":
    unittest.main()
