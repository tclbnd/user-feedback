"""Two-channel variable-speed replay with a pitch-preserving phase vocoder.

Usage: python3 stretch.py input.json output.json

Contract (see README.md):
  input  : rate (Hz), blocks ([[[l, r], ...], ...]), knots ([[t, speed], ...])
           knots are [output sample index, speed], first knot at 0, speed is
           linear between knots and held after the last one (range 0.7-1.6).
  output : frames ([[l, r], ...]), mapping (x(t) per output sample),
           transients (input and output indices), latency (frames).

Method
------
* Output clock is fixed (HOP samples).  The analysis position of output frame
  f is x(f*HOP), the per-sample integral of the speed curve, so block
  boundaries never change the continuous-time state.
* Sustained regions keep pitch through a classic phase vocoder.  Both channels
  share one phase advance computed from the mid spectrum; each channel keeps
  only its constant phase offset against the mid, so exact gain / polarity
  relations between L and R are preserved.
* Transients are detected in a first pass by adaptive spectral-flux peak
  picking on the mid channel.  At a transient frame the synthesis phase is
  reset to the analysis phase (fixed strategy), which keeps drum hits compact
  instead of smearing them into two attacks.  Window overlap and OLA
  normalisation are unchanged.
* Latency (NFFT lookahead) is reported; delivered coordinates are already
  compensated, i.e. output sample 0 corresponds to input coordinate 0.
"""
import argparse
import cmath
import json
import math
from bisect import bisect_left
from pathlib import Path
from statistics import median

MIN_SPEED = 0.7
MAX_SPEED = 1.6
NFFT = 256                 # analysis window (samples)
HOP = 32                   # fixed output clock (samples)
LATENCY = NFFT             # lookahead, compensated in delivered coordinates
FLUX_RATIO = 1.6           # transient: flux > ratio * running median
FLUX_FLOOR = 0.02          # ... and flux > floor * total previous magnitude
FLUX_HISTORY = 43
_TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------- speed map
class SpeedMap:
    """Output-clock speed curve; input coordinate x(t) is its integral."""

    def __init__(self, knots):
        if not knots:
            raise ValueError("at least one speed knot is required")
        pts = sorted(
            (int(t), min(MAX_SPEED, max(MIN_SPEED, float(s)))) for t, s in knots
        )
        if pts[0][0] > 0:
            pts.insert(0, (0, pts[0][1]))
        self._t = [p[0] for p in pts]
        self._s = [p[1] for p in pts]

    def speed(self, t):
        """Piecewise-linear speed at integer output sample t."""
        ts, ss = self._t, self._s
        if t <= ts[0]:
            return ss[0]
        if t >= ts[-1]:
            return ss[-1]
        i = max(1, bisect_left(ts, t))
        t0, t1 = ts[i - 1], ts[i]
        if t1 == t0:
            return ss[i]
        u = (t - t0) / (t1 - t0)
        return ss[i - 1] + (ss[i] - ss[i - 1]) * u

    def build(self, n_in):
        """Integrate the curve.  Returns (T, mapping) where T is the smallest
        integer with x(T) >= n_in and mapping[t] = x(t) for t in [0, T)."""
        cum = [0.0]
        while cum[-1] < n_in:
            cum.append(cum[-1] + self.speed(len(cum) - 1))
        return len(cum) - 1, cum[:-1]


# --------------------------------------------------------------------- FFT
_FFT_TABLES = {}


def _tables(n):
    tab = _FFT_TABLES.get(n)
    if tab is None:
        levels = int(round(math.log2(n)))
        rev = [0] * n
        for i in range(n):
            r = 0
            for b in range(levels):
                if i & (1 << b):
                    r |= 1 << (levels - 1 - b)
            rev[i] = r
        stages = []
        length = 2
        while length <= n:
            half = length // 2
            stages.append(
                [cmath.exp(complex(0.0, _TWO_PI * j / length)) for j in range(half)]
            )
            length *= 2
        tab = (rev, stages)
        _FFT_TABLES[n] = tab
    return tab


def _fft(a, invert=False):
    """Iterative radix-2 FFT, in place on a list of complex."""
    n = len(a)
    rev, stages = _tables(n)
    for i in range(n):
        j = rev[i]
        if i < j:
            a[i], a[j] = a[j], a[i]
    sign = 1.0 if invert else -1.0
    length = 2
    for table in stages:
        half = length // 2
        for i in range(0, n, length):
            k = i
            for w in table:
                if sign < 0.0:
                    w = w.conjugate()
                v = a[k + half] * w
                u = a[k]
                a[k] = u + v
                a[k + half] = u - v
                k += 1
        length *= 2
    if invert:
        inv = 1.0 / n
        for i in range(n):
            a[i] *= inv
    return a


# ------------------------------------------------------------- phase vocoder
def _frame_spectra(pad, off, a, win):
    """Windowed frame at fractional input position a -> per-channel spectra."""
    i0 = math.floor(a)
    frac = a - i0
    base = i0 + off  # analysis window starts at input coordinate a
    z = []
    for n in range(NFFT):
        j = base + n
        p0, p1 = pad[j], pad[j + 1]
        w = win[n]
        z.append(
            complex(
                (p0[0] * (1.0 - frac) + p1[0] * frac) * w,
                (p0[1] * (1.0 - frac) + p1[1] * frac) * w,
            )
        )
    _fft(z)
    # split packed spectrum: Z = XL + i*XR
    xl = [0j] * NFFT
    xr = [0j] * NFFT
    for k in range(NFFT):
        zk = z[k]
        zm = z[(NFFT - k) % NFFT].conjugate()
        xl[k] = 0.5 * (zk + zm)
        xr[k] = -0.5j * (zk - zm)
    return xl, xr


def _detect_transients(pad, off, n_in, mapping, total_out, win):
    """Pass 1: spectral-flux peak picking on the mid channel (shared by L/R).

    Returns (set of transient frame indices, [input indices], [output indices]).
    """
    flux = []
    tots = []
    prev_mag = None
    f = 0
    while f * HOP < total_out:
        xl, xr = _frame_spectra(pad, off, mapping[f * HOP], win)
        mag = [0.5 * abs(xl[k] + xr[k]) for k in range(NFFT)]
        if prev_mag is None:
            flux.append(0.0)
            tots.append(0.0)
        else:
            fl = 0.0
            for m, p in zip(mag, prev_mag):
                d = m - p
                if d > 0.0:
                    fl += d
            flux.append(fl)
            tots.append(sum(prev_mag))
        prev_mag = mag
        f += 1

    frames = set()
    for f in range(2, len(flux)):
        lo = max(0, f - FLUX_HISTORY)
        hist = flux[lo:f]
        if not hist:
            continue
        med = median(hist)
        if not (flux[f] > FLUX_RATIO * med and flux[f] > FLUX_FLOOR * tots[f]):
            continue
        # local peak within +-2 frames
        if any(flux[g] > flux[f] for g in range(max(0, f - 2), f)):
            continue
        if any(flux[g] >= flux[f] for g in range(f + 1, min(len(flux), f + 3))):
            continue
        if frames and f - max(frames) < 4:  # merge close duplicates
            continue
        frames.add(f)

    trans_in, trans_out = [], []
    for f in sorted(frames):
        # compensate window lookahead: event sits near the window centre
        tin = min(max(0, int(round(mapping[f * HOP] + NFFT / 2))), max(0, n_in - 1))
        trans_in.append(tin)
        trans_out.append(bisect_left(mapping, tin))
    return frames, trans_in, trans_out


def _stretch_core(src, n_in, mapping, total_out):
    """Stereo-linked phase vocoder on a fixed output clock.

    src: list of [l, r]; mapping[t]: input coordinate of output sample t.
    Returns (frames, transients_in, transients_out).
    """
    win = [0.5 - 0.5 * math.cos(_TWO_PI * n / NFFT) for n in range(NFFT)]
    omega = [_TWO_PI * k / NFFT for k in range(NFFT)]
    half = NFFT // 2
    pad = [[0.0, 0.0]] + [list(fr) for fr in src] + [[0.0, 0.0]] * (NFFT + 1)
    off = 1  # padded index of input sample 0

    transient_frames, trans_in, trans_out = _detect_transients(
        pad, off, n_in, mapping, total_out, win
    )

    ola = [[0.0, 0.0] for _ in range(NFFT)]
    wsum = [0.0] * NFFT
    out = []
    prev_phase = prev_a = None
    synth = None

    f = 0
    while f * HOP < total_out:
        a = mapping[f * HOP]
        xl, xr = _frame_spectra(pad, off, a, win)
        xm = [0.5 * (xl[k] + xr[k]) for k in range(NFFT)]

        # ---- shared phase handling
        phase = [cmath.phase(v) for v in xm]
        if synth is None or f in transient_frames:
            # fixed transient strategy: reset synthesis phase to analysis phase
            synth = list(phase)
        else:
            ha = a - prev_a
            if ha < 1e-9:
                ha = 1e-9
            r = HOP / ha
            for k in range(NFFT):
                d = phase[k] - prev_phase[k] - omega[k] * ha
                d = (d + math.pi) % _TWO_PI - math.pi
                synth[k] += omega[k] * HOP + d * r

        # ---- resynthesis: shared phase advance + per-channel fixed offset
        yl = [0j] * NFFT
        yr = [0j] * NFFT
        for k in range(half + 1):
            e = complex(math.cos(synth[k]), math.sin(synth[k]))
            mk = xm[k].conjugate()
            ul = xl[k] * mk
            nl = abs(ul)
            ul = ul / nl if nl > 1e-12 else 1.0 + 0.0j
            ur = xr[k] * mk
            nr = abs(ur)
            ur = ur / nr if nr > 1e-12 else 1.0 + 0.0j
            yl[k] = abs(xl[k]) * e * ul
            yr[k] = abs(xr[k]) * e * ur
        for k in range(half + 1, NFFT):
            yl[k] = yl[NFFT - k].conjugate()
            yr[k] = yr[NFFT - k].conjugate()
        z2 = [yl[k] + 1j * yr[k] for k in range(NFFT)]
        _fft(z2, invert=True)

        for n in range(NFFT):
            w = win[n]
            ola[n][0] += z2[n].real * w
            ola[n][1] += z2[n].imag * w
            wsum[n] += w * w
        for n in range(HOP):  # samples finalised by this output-clock tick
            ws = wsum[n]
            if ws > 1e-8:
                out.append([ola[n][0] / ws, ola[n][1] / ws])
            else:
                out.append([0.0, 0.0])
        ola = ola[HOP:] + [[0.0, 0.0] for _ in range(HOP)]
        wsum = wsum[HOP:] + [0.0] * HOP

        prev_phase, prev_a = phase, a
        f += 1

    del out[total_out:]
    while len(out) < total_out:
        out.append([0.0, 0.0])
    return out, trans_in, trans_out


# ------------------------------------------------------------------ frontend
def render(data):
    frames = [
        [float(fr[0]), float(fr[1])] for block in data["blocks"] for fr in block
    ]
    n_in = len(frames)
    smap = SpeedMap(data["knots"])
    total_out, mapping = smap.build(n_in)
    out, trans_in, trans_out = _stretch_core(frames, n_in, mapping, total_out)
    return {
        "frames": out,
        "mapping": mapping,
        "transients": {"input": trans_in, "output": trans_out},
        "latency": LATENCY,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),allow_nan=False)+"\n")


if __name__=="__main__":
    main()
