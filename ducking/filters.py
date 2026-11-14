"""Deterministic biquad / K-weighting filters and true-peak oversampling (pure Python).

All filters are Direct-Form I with explicit state, so a filter can be run either on
a full signal or in arbitrary sample chunks (even out of order) and produce
bit-identical output, provided chunk boundaries carry the stored state.
"""
import math


def rb_biquad(kind, fs, f0, gain_db=0.0, q=0.7071):
    """RBJ audio EQ cookbook coefficients, normalised so b0/a0 = 1 layout."""
    A = 10.0 ** (gain_db / 40.0)
    w = 2.0 * math.pi * f0 / fs
    cw, sw = math.cos(w), math.sin(w)
    alpha = sw / (2.0 * q)
    if kind == "highshelf":
        sa = 2.0 * math.sqrt(A) * alpha
        b0 = A * ((A + 1.0) + (A - 1.0) * cw + sa)
        b1 = -2.0 * A * ((A - 1.0) + (A + 1.0) * cw)
        b2 = A * ((A + 1.0) + (A - 1.0) * cw - sa)
        a0 = (A + 1.0) - (A - 1.0) * cw + sa
        a1 = 2.0 * ((A - 1.0) - (A + 1.0) * cw)
        a2 = (A + 1.0) - (A - 1.0) * cw - sa
    elif kind == "highpass":
        b0 = (1.0 + cw) / 2.0
        b1 = -(1.0 + cw)
        b2 = (1.0 + cw) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cw
        a2 = 1.0 - alpha
    else:
        raise ValueError(kind)
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def kweighting_coeffs(fs):
    """BS.1770 K-weighting: stage 1 high-shelf (+4 dB @ ~1681 Hz),
    stage 2 high-pass (~38 Hz). Coefficients depend only on fs."""
    shelf = rb_biquad("highshelf", fs, 1681.974450955533, 4.0, 0.7071752369554196)
    hpf = rb_biquad("highpass", fs, 38.13547087602444, 0.0, 0.5003270373238773)
    return shelf, hpf


class Biquad:
    """Stateful Direct-Form I biquad. State survives across chunk boundaries."""

    __slots__ = ("b", "z1", "z2")

    def __init__(self, coeffs):
        b0, b1, b2, a1, a2 = coeffs
        # store numerator incl. normalised b0; feedback coefficients are -a1, -a2
        self.b = (b0, b1, b2, -a1, -a2)
        self.z1 = 0.0
        self.z2 = 0.0

    def reset(self):
        self.z1 = 0.0
        self.z2 = 0.0

    def step(self, x):
        b0, b1, b2, c1, c2 = self.b
        y = b0 * x + self.z1
        self.z1 = b1 * x + c1 * y + self.z2
        self.z2 = b2 * x + c2 * y
        return y

    def run(self, samples):
        return [self.step(x) for x in samples]


def kweight(samples, fs):
    """Apply full K-weighting on a signal (convenience, full length)."""
    shelf, hpf = kweighting_coeffs(fs)
    f1, f2 = Biquad(shelf), Biquad(hpf)
    return f2.run(f1.run(samples))


# --------------------------------------------------------------------------- #
# 4x oversampling true-peak estimation (deterministic polyphase FIR)
# --------------------------------------------------------------------------- #
def _sinc(x):
    if x == 0.0:
        return 1.0
    px = math.pi * x
    return math.sin(px) / px


def polyphase_lanczos(L=4, m=4):
    """Return L polyphase FIR kernels (phase p, p=0..L-1) of a windowed-sinc
    anti-imaging filter, each of length 2*m. h[p][j] = h_full(j + p/L)."""
    phases = []
    for p in range(L):
        ker = []
        for j in range(-m, m):
            t = j + p / L
            v = _sinc(t)
            # Lanczos window of the full-rate prototype
            w = _sinc(t / m) if abs(t) < m else 0.0
            ker.append(v * w)
        phases.append(ker)
    return phases


def true_peak_db(samples, L=4, m=4):
    """Estimate true peak (dBTP, relative to full scale) via L-fold zero-stuff
    oversampling with a deterministic windowed-sinc polyphase filter."""
    phases = polyphase_lanczos(L, m)
    n = len(samples)
    peak = 0.0
    for i in range(n):
        for p in range(L):
            ker = phases[p]
            acc = 0.0
            for j in range(-m, m):
                idx = i + j
                if 0 <= idx < n:
                    acc += samples[idx] * ker[j + m]
            a = abs(acc)
            if a > peak:
                peak = a
    if peak <= 0.0:
        return -math.inf
    return 20.0 * math.log10(peak)
