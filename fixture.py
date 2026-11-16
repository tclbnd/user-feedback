#!/usr/bin/env python3
"""Deterministic AEC test fixture (pure standard library).

Generates:
  * a local reference playback signal x (coloured noise, speech-like bursts,
    one explicit silence gap);
  * a fixed acoustic echo path h (an FIR supplied/constructed here);
  * a piecewise-constant *limited-range* bulk delay d(t);
  * a near-end voice signal v active only during labelled double-talk
    intervals;
  * the microphone mix m(n) = conv(x, h) delayed by d(n) + v(n) (+ optional
    floor noise).

Everything is derived from a seeded LCG, so runs are fully reproducible.
"""
import math

from aec_core import DEFAULT_FS

LCG_A = 1103515245
LCG_C = 12345
LCG_M = 1 << 31


class Rng:
    def __init__(self, seed=12345):
        self.state = seed & 0x7fffffff

    def uniform(self):
        self.state = (LCG_A * self.state + LCG_C) % LCG_M
        return self.state / LCG_M

    def gauss(self):
        # Box-Muller
        u1 = max(self.uniform(), 1e-12)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def make_echo_path(taps=48, seed=7):
    """A fixed, normalised FIR echo path with a decaying room tail."""
    rng = Rng(seed)
    h = []
    for k in range(taps):
        # direct peak early, exponential room decay with random fine structure
        envelope = math.exp(-3.5 * k / taps)
        h.append(envelope * (0.6 + 0.4 * rng.uniform()) * rng.gauss())
    norm = math.sqrt(sum(c * c for c in h)) or 1.0
    return [c / norm for c in h]


def _one_pole_bandpass(noise, hp=0.85, lp=0.2):
    """Cheap speech-ish colouring: high-pass difference then low-pass pole."""
    out = []
    prev_in = 0.0
    for n in noise:
        hpv = hp * (n - prev_in)
        prev_in = n
        out.append(hpv)
    smoothed = []
    acc = 0.0
    for n in out:
        acc += lp * (n - acc)
        smoothed.append(acc)
    return smoothed


def make_reference(fs=DEFAULT_FS, duration=7.0, seed=1,
                   active_ranges=None, silence_gap=(2.5, 2.9)):
    """Coloured-noise far-end reference with on/off bursts and a silence."""
    n = int(fs * duration)
    rng = Rng(seed)
    if active_ranges is None:
        active_ranges = [(0.0, 2.4), (2.9, duration)]
    raw = [rng.gauss() for _ in range(n)]
    col = _one_pole_bandpass(raw)
    pwr = sum(v * v for v in col) / n
    scale = 1.0 / (math.sqrt(pwr) or 1.0)
    x = [v * scale for v in col]
    gate = [0.0] * n
    for a, b in active_ranges:
        for i in range(int(a * fs), min(n, int(b * fs))):
            gate[i] = 1.0
    sa, sb = int(silence_gap[0] * fs), int(silence_gap[1] * fs)
    for i in range(sa, min(n, sb)):
        gate[i] = 0.0
    return [x[i] * gate[i] for i in range(n)], gate


def make_nearend(fs, duration, dt_ranges, seed=99, level=0.9):
    """Near-end voice: coloured gated noise bursts, zero outside dt ranges."""
    n = int(fs * duration)
    rng = Rng(seed)
    raw = [rng.gauss() for _ in range(n)]
    col = _one_pole_bandpass(raw, hp=0.9, lp=0.35)
    gate = [0.0] * n
    labels = ["single-talk"] * n
    for a, b in dt_ranges:
        ia, ib = int(a * fs), min(n, int(b * fs))
        for i in range(ia, ib):
            gate[i] = 1.0
            labels[i] = "double-talk"
    pwr = sum(col[i] * col[i] * gate[i] for i in range(n)) / max(1, sum(gate))
    scale = level / (math.sqrt(pwr) or 1.0)
    v = [col[i] * gate[i] * scale for i in range(n)]
    return v, labels


def make_delay_schedule(fs, duration, schedule):
    """Piecewise-constant bulk delay within a bounded (limited) range."""
    n = int(fs * duration)
    d = [0] * n
    for start, samples in schedule:
        i0 = int(start * fs)
        for i in range(i0, n):
            d[i] = int(samples)
    return d


def convolve_fixed_path(x, h):
    """Echo before bulk delay: y(n) = sum_k h[k] x(n-k)."""
    n = len(x)
    L = len(h)
    y = [0.0] * n
    for i in range(n):
        s = 0.0
        k0 = max(0, i - n + 1)
        for k in range(k0, L):
            if i - k < 0:
                break
            s += h[k] * x[i - k]
        y[i] = s
    return y


def apply_delay(y, d):
    """Time-varying bulk delay: out(n) = y(n - d(n))."""
    n = len(y)
    out = [0.0] * n
    for i in range(n):
        j = i - d[i]
        if j >= 0:
            out[i] = y[j]
    return out


def build_fixture(fs=DEFAULT_FS, duration=7.0, seed=1,
                  dt_ranges=((1.2, 2.0), (4.6, 5.3)),
                  delay_schedule=((0.0, 80), (3.0, 200), (5.8, 120)),
                  echo_gain=0.8, noise_floor=1e-3, seed_noise=2024):
    """Return the complete fixture dict used by the acceptance harness."""
    n = int(fs * duration)
    x, gate = make_reference(fs, duration, seed=seed)
    h = make_echo_path()
    echo = convolve_fixed_path(x, h)
    d = make_delay_schedule(fs, duration, delay_schedule)
    delayed_echo = apply_delay(echo, d)
    v, labels = make_nearend(fs, duration, dt_ranges)
    rng = Rng(seed_noise)
    noise = [noise_floor * rng.gauss() for _ in range(n)]
    mic = [echo_gain * delayed_echo[i] + v[i] + noise[i] for i in range(n)]
    return {
        "fs": fs,
        "n": n,
        "duration": duration,
        "reference": x,
        "ref_gate": gate,
        "mic": mic,
        "nearend": v,
        "echo_path": h,
        "delay": d,
        "labels": labels,
        "dt_ranges": list(dt_ranges),
        "delay_schedule": list(delay_schedule),
        "echo_gain": echo_gain,
    }


if __name__ == "__main__":
    # quick self-check: mic energy should dominate where echo is active
    fx = build_fixture()
    print("samples", fx["n"], "taps", len(fx["echo_path"]),
          "delay set", sorted(set(fx["delay"])))
