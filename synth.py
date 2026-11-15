"""Synthetic test material with ground truth delays.

Everything is deterministic given a seed, so acceptance measurements are
reproducible.  Material is deliberately hostile: strong wandering
fundamentals (low frequency dominance), broadband bursts, additive noise,
reverb, differing track lengths, clock restarts and two-segment delays.
"""
from __future__ import annotations

import math
import random

import dsp


def speech_like(n, fs, rng):
    """Harmonic glides + broadband bursts (voice/instrument stand-in)."""
    out = [0.0] * n
    seg = max(32, int(0.09 * fs))
    i = 0
    while i < n:
        f0 = 90.0 + 75.0 * rng.random()
        amp = 0.35 + 0.65 * rng.random()
        length = min(seg, n - i)
        burst_at = int(length * rng.random())
        for j in range(length):
            t = (i + j) / float(fs)
            v = 0.0
            for h in range(1, 12):
                fh = f0 * h * (1.0 + 0.0007 * h)
                if fh > 0.45 * fs:
                    break
                v += (1.0 / h) * math.sin(2.0 * math.pi * fh * t + 0.3 * h)
            env = amp * (0.55 + 0.45 * math.sin(2.0 * math.pi * 1.3 * t))
            out[i + j] += v * env * 0.35
        blen = max(8, length // 12)
        for j in range(blen):
            idx = i + (burst_at + j) % length
            out[idx] += rng.gauss(0.0, 0.35)
        i += length
    return out


def pink_noise(n, rng, poles=3):
    out = [0.0] * n
    for p in range(poles):
        a = 0.55 ** (p + 1)
        g = 1.0 / (1.0 + a)
        y = 0.0
        w = 1.0 / (p + 1)
        for i in range(n):
            y = a * y + (1.0 - a) * rng.gauss(0.0, 1.0)
            out[i] += w * y * g
    return out


def white(n, rng):
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def rms(x):
    if not x:
        return 0.0
    s = 0.0
    for v in x:
        s += v * v
    return math.sqrt(s / len(x))


def scale_to_rms(x, target):
    r = rms(x)
    if r <= 1e-20:
        return list(x)
    g = target / r
    return [v * g for v in x]


def add_noise(x, snr_db, rng):
    """Add white noise at the requested SNR (dB relative to signal power)."""
    r = rms(x)
    if r <= 1e-20:
        return list(x)
    npow = r * r / (10.0 ** (snr_db / 10.0))
    sigma = math.sqrt(npow)
    return [v + rng.gauss(0.0, sigma) for v in x]


def reverb_ir(fs, rt60, rng, predelay_ms=0.0):
    n = max(16, int(rt60 * 1.3 * fs))
    decay = math.log(1000.0) / max(1.0, rt60 * fs)
    ir = [rng.gauss(0.0, 1.0) * math.exp(-decay * i) for i in range(n)]
    e = math.sqrt(sum(v * v for v in ir)) or 1.0
    ir = [v / e for v in ir]
    pre = int(predelay_ms * fs / 1000.0)
    if pre > 0:
        ir = [0.0] * pre + ir
    return ir


def apply_reverb(x, fs, rt60, rng, drr_db=-10.0, predelay_ms=0.0):
    """Direct sound at t=0 plus a diffuse tail at a specified DRR.

    ``drr_db`` is the direct-to-reverberant ratio in dB, so the amount of
    reverberation is stated physically rather than as an arbitrary wet mix.
    """
    n = max(16, int(rt60 * 1.3 * fs))
    decay = math.log(1000.0) / max(1.0, rt60 * fs)
    tail = [rng.gauss(0.0, 1.0) * math.exp(-decay * i) for i in range(n)]
    e_tail = sum(v * v for v in tail)
    if e_tail <= 0.0:
        return list(x)
    g = math.sqrt(1.0 / (e_tail * (10.0 ** (drr_db / 10.0))))
    pre = int(predelay_ms * fs / 1000.0)
    if pre > 0:
        ir = [0.0] * (pre + 1) + [v * g for v in tail]
        ir[pre] = 1.0
    else:
        ir = [1.0] + [v * g for v in tail]
    y = dsp.fft_convolve(x, ir)
    return y[: len(x)]


def same_room(x, fs, rt60, rng, drr_db=-10.0, tail_n=48):
    """A *shared* room: a short diffuse FIR applied to both captures.

    Using one deterministic FIR for both tracks is what a real multi-mic
    recording of one room looks like -- the reverberant tail of the source is
    common to both captures.  :func:`apply_reverb` (independent tails) models
    two unrelated rooms instead, which destroys the correlation the aligner is
    supposed to exploit and is not a fair test.
    """
    n = max(16, int(rt60 * fs))
    decay = math.log(1000.0) / max(1.0, rt60 * fs)
    tail = [rng.gauss(0.0, 1.0) * math.exp(-decay * i) for i in range(n)]
    e_tail = sum(v * v for v in tail)
    if e_tail <= 0.0:
        return list(x)
    g = math.sqrt(1.0 / (e_tail * (10.0 ** (drr_db / 10.0))))
    ir = [1.0] + [v * g for v in tail]
    y = dsp.fft_convolve(x, ir)
    return y[: len(x)]


def delay_fractional(x, shift, pad=64):
    """Exact band limited delay by ``shift`` samples (float), zero padded."""
    if shift == 0.0:
        return list(x)
    padded = list(x) + [0.0] * pad
    y = dsp.phase_rotate(padded, shift)
    return y


def delay_curve(x, delay_at, fs, pad=128, taps=16):
    """Apply a time varying delay given by ``delay_at`` (samples).

    ``delay_at(p)`` is called for each *output* sample position ``p`` and
    returns how much the output lags the source at that point, in samples.
    Implemented as direct band limited (Lanczos-windowed sinc) interpolation
    of the source at ``p - delay``, which is exact for band limited material
    and introduces no windowing or overlap-add artefacts -- important because
    the test material must have a *known* delay to compare against.
    """
    n = len(x)
    out = [0.0] * (n + pad)
    for p in range(n + pad):
        d = delay_at(float(p))
        t = p - d
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
                kern = s * (math.sin(math.pi * xx / taps) / (math.pi * xx / taps))
            else:
                continue
            acc += x[idx] * kern
        out[p] = acc
    return out


def track_pair(n, fs, delay_ms, seed=1, snr_db=None, rt60=None,
               length_ratio=1.0, drift_ppm=0.0, two_segment=None,
               ref_drr_db=-14.0, tgt_drr_db=-6.0):
    """Build (ref, tgt, truth) for a delay of ``delay_ms`` (target later).

    The target track is the one carrying the acoustic delay (arrives later
    than the reference).  ``snr_db`` is additive noise on the target relative
    to its own signal power; ``ref_drr_db`` / ``tgt_drr_db`` are the
    direct-to-reverberant ratios of the two captures.
    """
    rng = random.Random(seed)
    src = speech_like(n, fs, rng)
    src = add_noise(src, 45.0, rng)
    src = scale_to_rms(src, 0.25)

    if rt60:
        # one room, one IR: both captures hear the same reverberant source
        room_rng = random.Random(seed * 7919 + 13)
        shared = same_room(src, fs, rt60, room_rng,
                           drr_db=(ref_drr_db + tgt_drr_db) * 0.5)
        direct = list(src)
        # blend so each capture can have its own DRR: the direct part is the
        # dry source, the tail is the shared reverberation
        e_dir = sum(v * v for v in direct) or 1.0
        e_rev = sum(v * v for v in shared) or 1.0

        def blend(drr):
            gt = math.sqrt(e_dir / (e_rev * (10.0 ** (drr / 10.0))))
            tail = [shared[i] - direct[i] for i in range(len(direct))]
            return [direct[i] + gt * tail[i] for i in range(len(direct))]
        ref = blend(ref_drr_db)
        base = blend(tgt_drr_db)
    else:
        ref = list(src)
        base = list(src)

    n_tgt = int(round(n * length_ratio))
    tgt_src = list(base[:n_tgt])

    if two_segment:
        d1, d2, switch_s = two_segment
        switch = int(switch_s * fs)

        def curve(pos):
            return (d1 if pos < switch else d2) * fs / 1000.0

        if drift_ppm:
            def curve2(pos):
                base = curve(pos)
                return base + pos * drift_ppm * 1e-6
            tgt = delay_curve(tgt_src, curve2, fs)
        else:
            tgt = delay_curve(tgt_src, curve, fs)
    else:
        shift = delay_ms * fs / 1000.0
        if drift_ppm:
            def curve(pos):
                return shift + pos * drift_ppm * 1e-6
            tgt = delay_curve(tgt_src, curve, fs)
        else:
            tgt = delay_fractional(tgt_src, shift)

    tgt = tgt[: n_tgt + int(0.05 * fs)]
    if snr_db is not None:
        tgt = add_noise(tgt, snr_db, rng)
    ref = scale_to_rms(ref, 0.25)
    tgt = scale_to_rms(tgt, 0.25 * (0.7 + 0.3 * ((seed * 37) % 10) / 10.0))
    truth = {
        "delay_ms": delay_ms,
        "two_segment": two_segment,
        "drift_ppm": drift_ppm,
        "snr_db": snr_db,
        "rt60": rt60,
        "fs": fs,
    }
    return ref, tgt, truth


def multi_track(n, fs, specs, seed=1, rt60=None, ref_drr_db=-3.0,
                tgt_drr_db=-3.0, src_seed=None):
    """Build one reference plus several targets from a *single* source.

    ``specs`` is a list of dicts, one per target, each supporting
    ``delay_ms``, ``snr_db``, ``length_ratio``, ``drift_ppm``,
    ``two_segment`` and an optional ``name``.  All tracks share the same
    source signal (and, when ``rt60`` is set, the same room), so they are
    genuinely captures of one performance -- which is the only setting in
    which "alignment" is a meaningful question.  Each target's noise and
    level are independent, as in a real multi-mic take.
    """
    rng = random.Random(seed if src_seed is None else src_seed)
    src = speech_like(n, fs, rng)
    src = add_noise(src, 45.0, rng)
    src = scale_to_rms(src, 0.25)

    if rt60:
        room = same_room(src, fs, rt60, random.Random(seed * 7919 + 13),
                         drr_db=(ref_drr_db + tgt_drr_db) * 0.5)
        e_dir = sum(v * v for v in src) or 1.0
        e_rev = sum(v * v for v in room) or 1.0
        tail = [room[i] - src[i] for i in range(len(src))]

        def blend(drr):
            gt = math.sqrt(e_dir / (e_rev * (10.0 ** (drr / 10.0))))
            return [src[i] + gt * tail[i] for i in range(len(src))]
        ref = blend(ref_drr_db)
        base = blend(tgt_drr_db)
    else:
        ref = list(src)
        base = list(src)

    targets = []
    for i, spec in enumerate(specs):
        nrng = random.Random(seed + 1000 + i)
        delay_ms = float(spec.get("delay_ms", 0.0))
        ratio = float(spec.get("length_ratio", 1.0))
        n_tgt = max(1, int(round(n * ratio)))
        sig = list(base[:n_tgt])
        two = spec.get("two_segment")
        drift = float(spec.get("drift_ppm", 0.0))
        if two:
            d1, d2, switch_s = two
            switch = switch_s * fs

            def curve(p, d1=d1, d2=d2, switch=switch, drift=drift):
                d = d1 if p < switch else d2
                return (d * fs / 1000.0) + p * drift * 1e-6
            y = delay_curve(sig, curve, fs)
        elif drift:
            def curve(p, delay_ms=delay_ms, drift=drift):
                return (delay_ms * fs / 1000.0) + p * drift * 1e-6
            y = delay_curve(sig, curve, fs)
        else:
            y = delay_fractional(sig, delay_ms * fs / 1000.0)
        y = y[: n_tgt + int(0.05 * fs)]
        snr = spec.get("snr_db")
        if snr is not None:
            y = add_noise(y, snr, nrng)
        y = scale_to_rms(y, 0.25 * (0.75 + 0.05 * i))
        targets.append({
            "name": spec.get("name", "t%d" % (i + 1)),
            "signal": y,
            "delay_ms": delay_ms,
            "two_segment": two,
            "drift_ppm": drift,
        })
    ref = scale_to_rms(ref, 0.25)
    return ref, targets

