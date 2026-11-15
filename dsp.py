"""Pure-python DSP helpers for the multitrack aligner (stdlib only)."""
from __future__ import annotations

import cmath
import math
import struct
import wave

_PLANS = {}


def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def _make_plan(m):
    bits = m.bit_length() - 1
    rev = [0] * m
    for i in range(1, m):
        rev[i] = (rev[i >> 1] >> 1) | ((i & 1) << (bits - 1))
    stages = []
    length = 2
    while length <= m:
        half = length >> 1
        ang = -2.0 * math.pi / length
        tw = [cmath.exp(complex(0.0, ang * k)) for k in range(half)]
        stages.append((length, half, tw))
        length <<= 1
    return rev, stages


def plan(m):
    p = _PLANS.get(m)
    if p is None:
        p = _make_plan(m)
        _PLANS[m] = p
    return p


def fft_complex(z, inverse=False):
    m = len(z)
    if m <= 1:
        return [complex(v) for v in z]
    rev, stages = plan(m)
    a = [z[rev[i]] for i in range(m)]
    for length, half, tw in stages:
        for i in range(0, m, length):
            for k in range(half):
                w = tw[k] if not inverse else tw[k].conjugate()
                u = a[i + k]
                v = a[i + k + half] * w
                a[i + k] = u + v
                a[i + k + half] = u - v
    return a


def tw_table(n):
    key = ("tw", n)
    tbl = _PLANS.get(key)
    if tbl is None:
        half = n >> 1
        tbl = [cmath.exp(complex(0.0, -2.0 * math.pi * k / n)) for k in range(half + 1)]
        _PLANS[key] = tbl
    return tbl


def rfft(x, n=None):
    if n is None:
        n = next_pow2(len(x))
    if n & (n - 1):
        raise ValueError("FFT length must be a power of two")
    L = len(x)
    m = n >> 1
    z = [0j] * m
    for i in range(m):
        j = i << 1
        re = x[j] if j < L else 0.0
        im = x[j + 1] if j + 1 < L else 0.0
        z[i] = complex(re, im)
    Z = fft_complex(z)
    tw = tw_table(n)
    out = [0j] * (m + 1)
    z0 = Z[0]
    out[0] = complex(z0.real + z0.imag, 0.0)
    out[m] = complex(z0.real - z0.imag, 0.0)
    for k in range(1, m):
        zk = Z[k]
        zc = Z[m - k].conjugate()
        e = (zk + zc) * 0.5
        o = (zk - zc) * (-0.5j)
        out[k] = e + tw[k] * o
    return out


def irfft(X, n):
    if n & (n - 1):
        raise ValueError("FFT length must be a power of two")
    m = n >> 1
    tw = tw_table(n)
    z = [0j] * m
    x0 = X[0]
    xm = X[m]
    e0 = (x0 + xm) * 0.5
    o0 = (x0 - xm) * 0.5
    z[0] = complex(e0.real, o0.real)
    for k in range(1, m):
        xk = X[k]
        xkm = X[m - k].conjugate()
        e = (xk + xkm) * 0.5
        o = (xk - xkm) * 0.5 / tw[k]
        z[k] = e + o * 1j
    Z = fft_complex(z, inverse=True)
    scale = 1.0 / m
    out = [0.0] * n
    for i in range(m):
        zi = Z[i]
        out[2 * i] = zi.real * scale
        out[2 * i + 1] = zi.imag * scale
    return out


def hann(n, periodic=False):
    if n <= 1:
        return [1.0]
    d = n if periodic else n - 1
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / d) for i in range(n)]


def median(v):
    if not v:
        return 0.0
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n & 1 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def mad(v):
    if not v:
        return 0.0
    m = median(v)
    return median([abs(x - m) for x in v])


def percentile(v, q):
    if not v:
        return 0.0
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    f = pos - lo
    return s[lo] * (1.0 - f) + s[hi] * f


def db(x, floor=1e-20):
    return 10.0 * math.log10(x if x > floor else floor)


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def read_wav(path):
    """Read a WAV file, return (mono float list, sample_rate)."""
    w = wave.open(path, "rb")
    try:
        nch = w.getnchannels()
        width = w.getsampwidth()
        fs = w.getframerate()
        raw = w.readframes(w.getnframes())
    finally:
        w.close()
    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        cnt = len(raw) // 2
        vals = [v / 32768.0 for v in struct.unpack("<%dh" % cnt, raw)]
    elif width == 3:
        cnt = len(raw) // 3
        out = []
        for i in range(cnt):
            v = raw[3 * i] | (raw[3 * i + 1] << 8) | (raw[3 * i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            out.append(v / 8388608.0)
        vals = out
    elif width == 4:
        cnt = len(raw) // 4
        vals = [v / 2147483648.0 for v in struct.unpack("<%di" % cnt, raw)]
    else:
        raise ValueError("unsupported sample width %d" % width)
    if nch > 1:
        mono = [0.0] * (len(vals) // nch)
        for i in range(len(mono)):
            s = 0.0
            base = i * nch
            for c in range(nch):
                s += vals[base + c]
            mono[i] = s / nch
        vals = mono
    return vals, fs


def write_wav16(path, x, fs):
    w = wave.open(path, "wb")
    try:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(fs))
        ints = [int(max(-32768, min(32767, round(v * 32767.0)))) for v in x]
        w.writeframes(struct.pack("<%dh" % len(ints), *ints) if ints else b"")
    finally:
        w.close()


def phase_rotate(x, shift, nfft=None, out_len=None):
    """Band-limited fractional delay of ``x`` by ``shift`` samples.

    Positive ``shift`` delays (moves content later).  Uses a linear-phase
    rotation in the frequency domain, so the result is exact for
    band-limited content and is free of interpolation error inside the band.
    Returns a sequence of length ``out_len`` (default ``len(x)``).
    """
    n = len(x)
    if n == 0:
        return []
    if nfft is None:
        nfft = next_pow2(n)
    spec = rfft(x, nfft)
    m = nfft >> 1
    ang = -2.0 * math.pi * shift / nfft
    Y = [spec[k] * cmath.exp(complex(0.0, ang * k)) for k in range(m + 1)]
    y = irfft(Y, nfft)
    L = n if out_len is None else out_len
    scale = 1.0
    if L <= nfft:
        return [y[i] for i in range(L)]
    return y + [0.0] * (L - nfft)


def resample_fft(x, factor, taps=24):
    """Band-limited resampling by ``factor`` (output length ~ len(x)*factor).

    Evaluates the input at the output sample instants with a
    Lanczos-windowed sinc kernel, i.e. exact band-limited interpolation for
    content below the lower Nyquist.  Before evaluating, the input is
    low-pass filtered in the frequency domain at the *lower* of the two
    Nyquist frequencies, so downsampling does not alias.  Doing both steps
    explicitly keeps the operation easy to verify (a pure tone must come out
    at the same frequency and amplitude).
    """
    n = len(x)
    if n == 0:
        return []
    if abs(factor - 1.0) < 1e-12:
        return list(x)
    out_n = max(1, int(round(n * factor)))
    # --- anti-alias low-pass (only needed when reducing the rate) ----------
    if factor < 1.0:
        nfft = next_pow2(n)
        X = rfft(x, nfft)
        half = nfft >> 1
        cutoff = int(half * factor)
        for k in range(cutoff + 1, half + 1):
            X[k] = 0j
        xf = irfft(X, nfft)[:n]
    else:
        xf = list(x)
    # --- band-limited interpolation on the new grid -----------------------
    step = 1.0 / factor
    out = [0.0] * out_n
    for i in range(out_n):
        t = i * step
        q = int(math.floor(t))
        frac = t - q
        acc = 0.0
        for k in range(-taps + 1, taps + 1):
            idx = q + k
            if idx < 0 or idx >= n:
                continue
            xx = frac - k
            if xx == 0.0:
                kern = 1.0
            elif abs(xx) < taps:
                s = math.sin(math.pi * xx) / (math.pi * xx)
                kern = s * (math.sin(math.pi * xx / taps) /
                            (math.pi * xx / taps))
            else:
                continue
            acc += xf[idx] * kern
        out[i] = acc
    return out


def fft_convolve(x, h):
    """Linear convolution via FFT (output length len(x)+len(h)-1)."""
    if not x or not h:
        return []
    n = next_pow2(len(x) + len(h) - 1)
    X = rfft(x, n)
    H = rfft(h, n)
    Y = [X[k] * H[k] for k in range(len(X))]
    return irfft(Y, n)


def band_mask(nfft, fs, lo_hz, hi_hz, taper_hz=None):
    """Smooth 0..1 mask over bins 0..nfft/2 spaning [lo, hi]."""
    if taper_hz is None:
        taper_hz = max(20.0, 0.05 * (hi_hz - lo_hz))
    half = nfft >> 1
    m = [0.0] * (half + 1)
    for k in range(half + 1):
        f = k * fs / float(nfft)
        if f < lo_hz - taper_hz or f > hi_hz + taper_hz:
            m[k] = 0.0
        elif f < lo_hz:
            m[k] = 0.5 - 0.5 * math.cos(math.pi * (f - (lo_hz - taper_hz)) / taper_hz)
        elif f > hi_hz:
            m[k] = 0.5 + 0.5 * math.cos(math.pi * (f - hi_hz) / taper_hz)
        else:
            m[k] = 1.0
    return m


def sinc_eval(c, tau, taps=24):
    """Band-limited interpolation of a periodic sampled signal ``c``.

    ``tau`` is a (possibly fractional) index into ``c``; the series is
    periodic over ``len(c)`` (as produced by an inverse FFT).  Uses a
    Lanczos-windowed sinc kernel.
    """
    n = len(c)
    i0 = int(math.floor(tau))
    frac = tau - i0
    acc = 0.0
    for j in range(-taps + 1, taps + 1):
        x = frac - j
        if x == 0.0:
            kernel = 1.0
        elif abs(x) < taps:
            s = math.sin(math.pi * x) / (math.pi * x)
            kernel = s * (math.sin(math.pi * x / taps) / (math.pi * x / taps))
        else:
            continue
        acc += c[(i0 + j) % n] * kernel
    return acc

