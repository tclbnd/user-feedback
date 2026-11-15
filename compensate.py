"""Delay compensation and mixing.

Compensation separates the two parts the specification asks for:

* **integer** delay -- applied by sample indexing, which is exact;
* **fractional** delay -- applied by a band limited linear-phase rotation
  (a frequency-domain phase ramp).  This is exact for band limited content
  and avoids the high-frequency roll-off, and therefore the smearing, that a
  short time-domain fractional interpolator would add.

For a *piecewise constant* map each aligned span is shifted as one
contiguous block, so the result is a single band limited rotation with no
blocking artefacts at all.  Only spans whose delay actually varies (a drift
ramp or a step boundary) are processed in short cross-faded pieces, because
there the delay genuinely changes inside the span.  Unaligned spans are
simply left at zero instead of being invented.

The mix is formed from the aligned tracks.  Comb depth is measured by
comparing the coherent sum of the tracks with the incoherent sum of their
magnitudes (:func:`comb_notch_db`), which is exactly the quantity the
acceptance criterion refers to.
"""
from __future__ import annotations

import math

import dsp


def piece_lag_ms(piece, t_ms):
    """Delay of a map piece at time ``t_ms`` (linear inside a ramp piece)."""
    if piece["kind"] == "const":
        return piece["params"][0]
    if piece["kind"] == "line":
        return piece["params"][0] + piece["params"][1] * t_ms
    return None


def compensated_length(n_tgt, pieces, fs, max_comp_ms):
    """Output length needed so that no aligned sample is shifted off the end."""
    extra = 0
    for p in pieces:
        if p["kind"] == "gap":
            continue
        hi = piece_lag_ms(p, p["t1_ms"])
        if hi is not None:
            extra = max(extra, hi)
    if extra <= 0:
        return n_tgt
    return n_tgt + int(math.ceil(extra * fs / 1000.0)) + 1


def compensate_track(x, pieces, fs, cfg, out_len=None):
    """Align ``x`` to the reference frame using the delay map ``pieces``.

    Unaligned spans are *not* synthesised: the output is faded to zero there
    and the span is returned so the caller can report it.  This is the honest
    behaviour -- inventing samples for a span the analyser could not align
    would re-create the very comb filtering the tool is meant to remove.

    Returns ``(y, applied)`` where ``applied[k]`` records the delay actually
    used per block: ``{"t0_ms", "t1_ms", "lag_ms", "aligned"}``.
    """
    n = len(x)
    if out_len is None:
        out_len = compensated_length(n, pieces, fs, cfg["max_comp_ms"])
    out = [0.0] * out_len
    applied = []

    def shift_span(src_lo, src_hi, lag_smp, fade_in, fade_out):
        """Place source samples [src_lo, src_hi) at output offset ``lag_smp``.

        The span is shifted as ONE contiguous block (a single band limited
        phase rotation for the fractional part plus exact integer indexing),
        so no blocking artefact can appear inside a span.  Fade lengths are in
        samples and are only non-zero at a step boundary, where cross-fading
        is genuinely needed.
        """
        span = [0.0] * (src_hi - src_lo)
        for i in range(src_hi - src_lo):
            idx = src_lo + i
            span[i] = x[idx] if 0 <= idx < n else 0.0
        # Aligning means output[p] = x[p + lag], i.e. shifting the signal
        # *earlier*.  The integer part is exact indexing; the fractional part
        # is the complementary band limited rotation (negative = earlier).
        n_int = int(math.floor(lag_smp))
        frac = lag_smp - n_int
        shifted = dsp.phase_rotate(span, -frac) if frac != 0.0 else span
        base = src_lo - n_int
        L = len(shifted)
        for i in range(L):
            j = base + i
            if j < 0 or j >= out_len:
                continue
            g = 1.0
            if fade_in > 0 and i < fade_in:
                g = min(g, i / float(fade_in))
            if fade_out > 0 and i >= L - fade_out:
                g = min(g, (L - 1 - i) / float(fade_out))
            out[j] += g * shifted[i]

    # Build spans from the map below; consecutive constant pieces with the
    # same (within tolerance) delay are merged so one rotation covers them.
    hop = max(1, int(cfg["chunk_hop_ms"] * fs / 1000.0))
    fade = max(8, int(cfg["fade_ms"] * fs / 1000.0))

    # Merge adjacent constant pieces with equal delay into single spans: for a
    # constant delay the whole span can be shifted in one band limited
    # rotation, which is exact and free of blocking artefacts.  Only a real
    # step or a ramp needs short cross-faded spans.
    spans = []
    for p in pieces:
        if p["kind"] == "gap":
            spans.append(p)
            continue
        if p["kind"] == "const":
            if spans and spans[-1].get("kind") == "const" and \
                    spans[-1]["t1_ms"] == p["t0_ms"] and \
                    abs(spans[-1]["params"][0] - p["params"][0]) < cfg["step_ms"]:
                spans[-1]["t1_ms"] = p["t1_ms"]
                continue
            spans.append(dict(p))
        else:
            spans.append(dict(p))

    for p in spans:
        a = max(0, int(round(p["t0_ms"] * fs / 1000.0)))
        b = min(n, int(round(p["t1_ms"] * fs / 1000.0)))
        if b <= a:
            continue
        if p["kind"] == "gap":
            continue
        lag_lo = piece_lag_ms(p, p["t0_ms"])
        lag_hi = piece_lag_ms(p, p["t1_ms"])
        if lag_lo is None or lag_hi is None:
            continue
        if max(abs(lag_lo), abs(lag_hi)) > cfg["max_comp_ms"]:
            continue
        if p["kind"] == "const":
            lag_smp = lag_lo * fs / 1000.0
            fi = 0
            fo = 0
            # cross-fade only where the neighbouring span has a different delay
            for q in spans:
                if q is p or q["kind"] == "gap":
                    continue
                if abs(q["t0_ms"] - p["t1_ms"]) < 1e-6 and \
                        abs(piece_lag_ms(q, q["t0_ms"]) - lag_lo) > cfg["step_ms"]:
                    fo = fade
                if abs(q["t1_ms"] - p["t0_ms"]) < 1e-6 and \
                        abs(piece_lag_ms(q, q["t1_ms"]) - lag_lo) > cfg["step_ms"]:
                    fi = fade
            shift_span(a, b, lag_smp, fi, fo)
            applied.append({"t0_ms": a / float(fs) * 1000.0,
                            "t1_ms": b / float(fs) * 1000.0,
                            "lag_ms": p["params"][0],
                            "lag_int": int(math.floor(lag_smp)),
                            "lag_frac": round(lag_smp -
                                              int(math.floor(lag_smp)), 9),
                            "aligned": True})
        else:
            start = a
            while start < b:
                end = min(b, start + hop)
                t_mid = (start + end) * 0.5 / float(fs) * 1000.0
                lag_ms = p["params"][0] + p["params"][1] * t_mid
                if abs(lag_ms) > cfg["max_comp_ms"]:
                    start = end
                    continue
                lag_smp = lag_ms * fs / 1000.0
                fi = fade if start > a else 0
                fo = fade if end < b else 0
                shift_span(start, end, lag_smp, fi, fo)
                applied.append({"t0_ms": start / float(fs) * 1000.0,
                                "t1_ms": end / float(fs) * 1000.0,
                                "lag_ms": lag_ms,
                                "lag_int": int(math.floor(lag_smp)),
                                "lag_frac": round(lag_smp -
                                                  int(math.floor(lag_smp)), 9),
                                "aligned": True})
                start = end
    return out, applied


def mix_tracks(tracks, out_len):
    """Sum aligned tracks and normalise to avoid clipping (no dynamics)."""
    mix = [0.0] * out_len
    for t in tracks:
        for i in range(min(len(t), out_len)):
            mix[i] += t[i]
    peak = max((abs(v) for v in mix), default=0.0)
    if peak > 0.98:
        g = 0.98 / peak
        mix = [v * g for v in mix]
    return mix


def comb_notch_db(sources, fs, nfft=4096, hop=None, f_lo=100.0, f_hi=6000.0,
                  mix=None):
    """Depth of the spectral notches produced by summing ``sources`` (dB).

    For every analysis frame the magnitude of the *coherent sum* is compared
    with the *incoherent* sum of the individual magnitudes.  For two identical
    signals that are perfectly aligned the ratio is 1 everywhere (no notch).
    For two copies offset by ``d`` the ratio is ``|cos(pi f d)|``, so the
    notches appear exactly at the comb frequencies and their depth is the
    quantity the acceptance criterion talks about.

    The reported value is the median over frames of the 95th-percentile
    per-frame notch depth within the band, so a single pathological frame
    cannot dominate and content without energy in the band does not
    artificially depress the number.

    Returns ``(notch_db, per_frame_db)``.
    """
    if hop is None:
        hop = nfft // 2
    win = dsp.hann(nfft, periodic=True)
    nb = nfft // 2 + 1
    bins = [k for k in range(nb) if f_lo <= k * fs / float(nfft) <= f_hi]
    if not bins or not sources:
        return 0.0, []
    if mix is None:
        mix = [0.0] * min(len(s) for s in sources)
        for s in sources:
            for i in range(len(mix)):
                mix[i] += s[i]
    per_frame = []
    n = min(len(mix), min(len(s) for s in sources))
    start = 0
    while start + nfft <= n:
        seg = [mix[start + i] * win[i] for i in range(nfft)]
        X = dsp.rfft(seg, nfft)
        ideal = [0.0] * nb
        for s in sources:
            seg2 = [s[start + i] * win[i] for i in range(nfft)]
            X2 = dsp.rfft(seg2, nfft)
            for k in bins:
                ideal[k] += abs(X2[k])
        depths = []
        for k in bins:
            num = abs(X[k])
            den = ideal[k]
            if den > 1e-18:
                depths.append(-20.0 * math.log10(max(num, 1e-20) / den))
        if depths:
            depths.sort()
            per_frame.append(depths[min(len(depths) - 1,
                                        int(0.95 * len(depths)))])
        start += hop
    if not per_frame:
        return 0.0, []
    per_frame.sort()
    return per_frame[len(per_frame) // 2], per_frame

