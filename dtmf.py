#!/usr/bin/env python3
"""Offline DTMF pair detection and click-free equal-length stereo mute.

Command line::

    python3 dtmf.py input.json output.json

Input JSON::

    {"samples": [left, right], "sample_rate": 8000, "tone_ms": 40,
     "threshold": 123.0, "fade": 80}

Output JSON::

    {"events": [{"key", "start", "end", "confidence"}],
     "left": [...], "right": [...], "twist": {"1": 1.234}}

Pipeline
--------
1. A ``tone_ms`` analysis window is slid over each channel with a
   quarter-window hop (a full-length tone can never fall between windows).
   A key is accepted only when its row and column bins are both present,
   each dominates its own DTMF group, both are narrow-band, and the pair
   holds most of the window's energy (``(row+col)/(0.5*N*sum(x^2))``).
   A single frequency -- or broadband speech that happens to put energy on
   a row/column pair -- therefore never produces a hit.
2. Same-key hits on one channel are merged while their gap is shorter than
   one window, as required by the channel-offset rule.
3. Each merged run is refined with a ~20 ms amplitude envelope.  The ramp of
   each component is fitted and extrapolated to zero, which recovers the real
   onset and release of the tone instead of the (up to one window wide) coarse
   analysis bounds.  The refined span is checked against ``tone_ms``, which
   is what makes the minimum-duration rule exact for partial windows: a
   30 ms tone is rejected, a 40 ms one accepted.
4. A key becomes an event only when both channels hold a matching valid run
   whose onsets are at most one window apart (channel delay up to
   ``tone_ms``).
5. Each channel is muted over *its own* span.  A half-cosine ramp of
   ``fade`` samples is placed immediately outside every span, so the last
   unmuted sample and the first restored sample are both at nearly zero
   gain and no click is produced.  Output length always equals input length
   and the channel alignment is unchanged.
"""

import json
import math
import sys

ROWS = [697.0, 770.0, 852.0, 941.0]
COLS = [1209.0, 1336.0, 1477.0, 1633.0]
KEYS = ["123A", "456B", "789C", "*0#D"]

EPS = 1e-12

# Pair validation.  The limits are loose enough for real DTMF (modest twist,
# background noise) yet strict enough to reject a lone frequency or broadband
# speech that happens to put energy on a DTMF grid point.
MIN_GROUP_DOMINANCE = 1.25     # best bin vs. next bin of the same group
MIN_GROUP_CONCENTRATION = 0.35  # best bin vs. the whole group
MIN_PAIR_FRACTION = 0.35       # (row+col) energy vs. total window energy
MAX_TWIST_RATIO = 20.0         # column/row imbalance accepted (~13 dB amp)
DURATION_TOLERANCE = 0.95      # minimum accepted span / tone_ms window

# A coherent sinusoid of amplitude A in an N-sample window has
# |X|^2 ~ (A*N/2)^2 while sum(x^2) ~ A^2*N/2, so |X|^2 / (0.5*N*sum(x^2))
# is ~1.0 for a pure tone at that bin.  A DTMF pair therefore scores ~0.5+0.5
# = 1.0, while voiced speech with formants on those very bins scores < 0.15.
PAIR_NORM = 0.5


# --------------------------------------------------------------------------
# spectrum helpers
# --------------------------------------------------------------------------

def goertzel(sig, freq, sr):
    """Goertzel bin energy (|X|^2) of ``sig`` at ``freq`` for sample rate sr."""
    if not sig:
        return 0.0
    coeff = 2.0 * math.cos(2.0 * math.pi * freq / sr)
    s1 = s2 = 0.0
    for x in sig:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def _key_frequencies(key):
    """Row/column frequency for a matrix key such as ``'5'`` or ``'D'``."""
    for ri, row in enumerate(KEYS):
        ci = row.find(key)
        if ci >= 0:
            return ROWS[ri], COLS[ci]
    raise ValueError("not a DTMF key: %r" % (key,))


def _group_peak(seg, sr, freqs, thr, bin_hz):
    """Return ``(index, energy)`` of a dominant bin of one DTMF group.

    A bin qualifies when it carries at least ``thr`` energy, dominates the
    other frequencies of its group and is narrow-band (one analysis bin away
    must lose most of the energy).
    """
    energies = [goertzel(seg, f, sr) for f in freqs]
    idx = 0
    for i in range(1, len(energies)):
        if energies[i] > energies[idx]:
            idx = i
    peak = energies[idx]
    if peak < thr or peak <= 0.0:
        return None

    second = 0.0
    for i, e in enumerate(energies):
        if i != idx and e > second:
            second = e
    if peak < MIN_GROUP_DOMINANCE * second:
        return None

    total = sum(energies)
    if total <= 0.0 or peak < MIN_GROUP_CONCENTRATION * total:
        return None

    # Narrow-band proof: a broad spectrum (speech, clicks) keeps a large
    # share of its energy one analysis bin away.
    side = 0.0
    for df in (bin_hz, -bin_hz):
        probe = freqs[idx] + df
        if 0.0 < probe < 0.5 * sr:
            e = goertzel(seg, probe, sr)
            if e > side:
                side = e
    if side > EPS and peak < MIN_GROUP_DOMINANCE * side:
        return None
    return idx, peak


def _pair_fraction(seg, row_e, col_e):
    """Fraction of the window's total energy held by the two DTMF bins.

    ``|X|^2`` is normalised by ``0.5*N*sum(x^2)``, which is 1.0 for a pure
    coherent sine at that bin.  A DTMF pair therefore scores ~1.0 while
    voiced speech with formants on the same bins scores well below 0.3.
    """
    total = 0.0
    for x in seg:
        total += x * x
    if total <= EPS:
        return 0.0
    norm = PAIR_NORM * len(seg) * total
    return (row_e + col_e) / norm


# --------------------------------------------------------------------------
# coarse detection
# --------------------------------------------------------------------------

def _analyse_channel(sig, sr, win, hop, thr):
    """Sliding-window tone-pair hits for one channel."""
    n = len(sig)
    if win <= 0 or n < win or thr <= 0.0:
        return []
    bin_hz = float(sr) / float(win)
    offsets = list(range(0, n - win + 1, hop))
    if not offsets or offsets[-1] != n - win:
        offsets.append(n - win)

    hits = []
    for off in offsets:
        seg = sig[off:off + win]
        row = _group_peak(seg, sr, ROWS, thr, bin_hz)
        if row is None:
            continue
        col = _group_peak(seg, sr, COLS, thr, bin_hz)
        if col is None:
            continue
        ri, row_energy = row
        ci, col_energy = col
        ratio = col_energy / row_energy if row_energy > EPS else float("inf")
        if ratio > MAX_TWIST_RATIO or ratio < 1.0 / MAX_TWIST_RATIO:
            continue
        if _pair_fraction(seg, row_energy, col_energy) < MIN_PAIR_FRACTION:
            continue
        hits.append({
            "key": KEYS[ri][ci],
            "start": off,
            "end": off + win,
            "row_energy": row_energy,
            "col_energy": col_energy,
        })
    return hits


def _hits_to_runs(hits, win):
    """Merge same-key hits whose gap is shorter than one analysis window."""
    runs = []
    for h in sorted(hits, key=lambda x: (x["start"], x["end"])):
        if (runs and runs[-1]["key"] == h["key"]
                and h["start"] - runs[-1]["end"] < win):
            run = runs[-1]
            run["end"] = max(run["end"], h["end"])
            run["hits"].append(h)
        else:
            runs.append({"key": h["key"], "start": h["start"],
                         "end": h["end"], "hits": [h]})
    return runs


# --------------------------------------------------------------------------
# boundary refinement
# --------------------------------------------------------------------------

def _components(amp, gate):
    """Indices of envelope regions above ``gate``; 1-3 dropped frames bridge."""
    comps = []
    m = len(amp)
    i = 0
    while i < m:
        if amp[i] < gate:
            i += 1
            continue
        j = i
        while j + 1 < m:
            if amp[j + 1] >= gate:
                j += 1
            elif j + 2 < m and amp[j + 2] >= gate:
                j += 2
            elif j + 3 < m and amp[j + 3] >= gate:
                j += 3
            else:
                break
        comps.append((i, j))
        i = j + 1
    return comps


def _plateau(values):
    """Median of the upper half of a component, robust to ripple spikes."""
    ordered = sorted(values)
    top = ordered[len(ordered) // 2:]
    if not top:
        return 0.0
    mid = len(top) // 2
    if len(top) % 2:
        return top[mid]
    return 0.5 * (top[mid - 1] + top[mid])


def _monotone_edge(values, boundary, direction, plateau, limit):
    """Ramp samples of one edge, walking away from the component boundary.

    The walk stops at the plateau or when the envelope returns to the floor.
    Only the leading monotone stretch is kept, so ripple on the plateau can
    never bend the fit.
    """
    pts = []
    idx = boundary
    steps = 0
    n = len(values)
    while 0 <= idx < n and steps <= limit:
        a = values[idx]
        if a >= 0.92 * plateau:
            break
        if a < 0.04 * plateau:
            if pts:
                break
        else:
            if pts:
                # Walking away from the component the envelope must fall:
                # that is true both before the onset and after the release.
                # An increase is ripple and ends the fitted ramp.
                if a > values[pts[-1]] + 0.02 * plateau:
                    break
            pts.append(idx)
        idx += direction
        steps += 1
    # pts[0] is the first non-floor frame, which may be only just covered.
    while len(pts) > 1 and values[pts[0]] < 0.05 * plateau:
        pts.pop(0)
    if len(pts) < 2:
        return []
    return pts


def _ramp_time(values, starts, idxs):
    """Sub-sample time at which the ramp leaves the floor.

    ``idxs`` is already ripple-filtered and monotone.  A least-squares line
    through the ramp samples is extrapolated to zero amplitude; for a
    coherent tone the windowed amplitude is linear in the covered fraction of
    the window, so this recovers the instant at which the analysis window
    stops/starts touching the tone.
    """
    if len(idxs) < 2:
        return None
    n = float(len(idxs))
    sx = sy = sxx = sxy = 0.0
    for x in idxs:
        t = float(starts[x])
        a = max(values[x], 0.0)
        sx += t
        sy += a
        sxx += t * t
        sxy += t * a
    den = n * sxx - sx * sx
    if abs(den) < EPS:
        return None
    slope = (n * sxy - sx * sy) / den
    if abs(slope) < EPS:
        return None
    t = -(sy - slope * sx) / n / slope
    if not math.isfinite(t):
        return None
    return t


def _refine_run(sig, sr, run, win, thr, min_duration, hop):
    """Return ``{"key", "span": (start, end), "hits"}`` or ``None``.

    ``span`` covers the real tone, not the coarse window bounds.
    """
    n = len(sig)
    base = max(0, run["start"] - hop)
    limit = min(n, run["end"] + hop)
    # A ~20 ms envelope window keeps an adjacent DTMF tone's leakage small
    # compared with the tone under test, while the ramp still spans several
    # analysis frames for a precise fit.
    rwin = int(round(0.020 * sr))
    rwin = max(16, min(rwin, win))
    if limit - base < rwin:
        return None

    row_f, col_f = _key_frequencies(run["key"])
    step = max(1, rwin // 16)
    pad = max(1, int(round(0.001 * sr)))

    # The coarse run only contains windows that passed the pair test, so its
    # bounds can be inside the tone when a loud neighbour masks the first or
    # last few windows.  If the envelope is still above the gate at a range
    # edge, widen that edge by one analysis window and try again.
    amp = starts = None
    for _ in range(3):
        if limit - base < rwin:
            return None
        starts = list(range(base, limit - rwin + 1, step))
        if len(starts) < 3:
            return None
        amp = []
        for s in starts:
            seg = sig[s:s + rwin]
            # Both members of the pair must be present: take the weaker one.
            amp.append(math.sqrt(max(0.0, min(goertzel(seg, row_f, sr),
                                               goertzel(seg, col_f, sr)))))
        peak = max(amp)
        if peak <= EPS:
            return None
        comps = _components(amp, 0.30 * peak)
        if not comps:
            return None
        grow_left = comps[0][0] <= 0
        grow_right = comps[-1][1] >= len(amp) - 1
        if not (grow_left or grow_right):
            break
        if grow_left:
            base = max(0, base - win)
        if grow_right:
            limit = min(n, limit + win)
        if base == 0 and limit == n:
            break

    events = []
    for i, j in comps:
        local = _plateau(amp[i:j + 1])
        if local <= EPS:
            continue

        edge_limit = max(2, int(math.ceil(3.0 * rwin / step)))
        rise_idx = _monotone_edge(amp, i, -1, local, edge_limit)
        fall_idx = _monotone_edge(amp, j, +1, local, edge_limit)

        # Refined onset/offset.  The ramp measures window coverage, so the
        # tone starts one window after the ramp's zero and ends at it.
        zero_in = _ramp_time(amp, starts, rise_idx)
        if zero_in is None:
            zero_in = starts[i] - 0.5 * rwin
        zero_out = _ramp_time(amp, starts, fall_idx)
        if zero_out is None:
            zero_out = starts[j] + 0.5 * rwin

        onset = max(float(base), min(float(limit), zero_in + rwin - pad))
        offset = max(float(base), min(float(limit), zero_out + pad))
        if offset - onset > 1e-9:
            events.append((onset, offset))

    if not events:
        return None

    # All components of this run belong to the same key and are separated by
    # less than one analysis window (otherwise they would be separate runs),
    # so they form ONE event: mute from the first onset to the last offset.
    # Leaving interior gaps unmuted would let tone residue through wherever
    # a loud neighbour briefly masked the envelope.
    start = int(math.floor(min(s for s, _ in events)))
    end = int(math.ceil(max(e for _, e in events)))
    if end - start + 1e-9 < min_duration:
        return None
    return {"key": run["key"], "span": (max(0, start), min(n, end)),
            "hits": run["hits"]}


# --------------------------------------------------------------------------
# pairing, muting, public API
# --------------------------------------------------------------------------

def _span_gap(a0, a1, b0, b1):
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0


def _gain_track(length, spans, fade):
    """Per-sample gain: 1 everywhere except spans (0) and ramp shoulders."""
    gain = [1.0] * length
    fade = max(0, int(fade))
    for raw_start, raw_end in spans:
        start = max(0, int(raw_start))
        end = min(length, int(raw_end))
        if end <= start:
            continue
        for i in range(start, end):
            gain[i] = 0.0
        if fade <= 0:
            continue
        for k in range(fade):
            i = start - fade + k                 # down-ramp before the span
            if 0 <= i < length:
                g = 0.5 * (1.0 + math.cos(math.pi * (k + 0.5) / fade))
                if g < gain[i]:
                    gain[i] = g
            j = end + k                          # up-ramp after the span
            if 0 <= j < length:
                g = 0.5 * (1.0 - math.cos(math.pi * (k + 0.5) / fade))
                if g < gain[j]:
                    gain[j] = g
    return gain


def _apply_gain(samples, gain):
    as_int = all(isinstance(v, int) for v in samples)
    out = [0] * len(samples)
    for i, v in enumerate(samples):
        g = gain[i] if i < len(gain) else 1.0
        if g >= 1.0:
            out[i] = v
        elif as_int:
            out[i] = int(round(v * g))
        else:
            out[i] = v * g
    return out


def _refined_runs(left, right, sr, win, hop, thr, min_duration):
    lruns = []
    for run in _hits_to_runs(_analyse_channel(left, sr, win, hop, thr), win):
        refined = _refine_run(left, sr, run, win, thr, min_duration, hop)
        if refined is not None:
            lruns.append(refined)
    rruns = []
    for run in _hits_to_runs(_analyse_channel(right, sr, win, hop, thr), win):
        refined = _refine_run(right, sr, run, win, thr, min_duration, hop)
        if refined is not None:
            rruns.append(refined)
    return lruns, rruns


def _pair_runs(lruns, rruns, win):
    """Pair same-key runs across channels.

    Per the specification the two channels may be offset by up to one
    ``tone_ms`` window.  Onset skew (not span gap) is the bounded quantity,
    because both spans are full tone lengths.  Candidate order: smallest
    skew, then smallest gap, so a delayed channel pairs with the run that
    actually belongs to it.
    """
    candidates = []
    for li, lrun in enumerate(lruns):
        for ri, rrun in enumerate(rruns):
            if lrun["key"] != rrun["key"]:
                continue
            skew = abs(lrun["span"][0] - rrun["span"][0])
            if skew <= win:
                gap = _span_gap(lrun["span"][0], lrun["span"][1],
                                rrun["span"][0], rrun["span"][1])
                candidates.append((skew, gap, li, ri))
    candidates.sort()
    used_l, used_r = set(), set()
    pairs = []
    for _skew, _gap, li, ri in candidates:
        if li in used_l or ri in used_r:
            continue
        used_l.add(li)
        used_r.add(ri)
        pairs.append((lruns[li], rruns[ri]))
    return pairs


def _build_events(pairs, thr):
    events, left_spans, right_spans, twist = [], [], [], {}
    for lrun, rrun in pairs:
        start = min(lrun["span"][0], rrun["span"][0])
        end = max(lrun["span"][1], rrun["span"][1])
        hits = lrun["hits"] + rrun["hits"]
        conf = 0.0
        if thr > 0.0:
            conf = min(min(h["row_energy"], h["col_energy"]) / thr
                       for h in hits)
        events.append({"key": lrun["key"], "start": int(start),
                       "end": int(end), "confidence": round(conf, 3),
                       "left_span": tuple(int(v) for v in lrun["span"]),
                       "right_span": tuple(int(v) for v in rrun["span"])})
        left_spans.append(lrun["span"])
        right_spans.append(rrun["span"])
        ratio = max((h["col_energy"] / h["row_energy"]
                     if h["row_energy"] > EPS else 0.0) for h in hits)
        twist[lrun["key"]] = max(twist.get(lrun["key"], 0.0), ratio)
    order = sorted(range(len(events)),
                   key=lambda i: (events[i]["start"], events[i]["end"],
                                  events[i]["key"]))
    events = [events[i] for i in order]
    left_spans = [left_spans[i] for i in order]
    right_spans = [right_spans[i] for i in order]
    twist = {k: round(v, 3) for k, v in sorted(twist.items())}
    return events, left_spans, right_spans, twist


def detect(samples, sample_rate, tone_ms, threshold, fade=0):
    """Detect DTMF pairs and build equal-length muted channels.

    Returns the JSON-ready dict ``{"events", "left", "right", "twist"}``.
    """
    if not samples:
        return {"events": [], "left": [], "right": [], "twist": {}}
    left = list(samples[0])
    right = list(samples[1]) if len(samples) > 1 else list(samples[0])

    sr = float(sample_rate)
    win = max(2, int(round(float(tone_ms) * sr / 1000.0)))
    hop = max(1, win // 4)
    thr = float(threshold)
    min_duration = max(1, int(round(win * DURATION_TOLERANCE)))

    lruns, rruns = _refined_runs(left, right, sr, win, hop, thr, min_duration)
    pairs = _pair_runs(lruns, rruns, win)
    events, left_spans, right_spans, twist = _build_events(pairs, thr)
    public = [{"key": e["key"], "start": e["start"], "end": e["end"],
               "confidence": e["confidence"]} for e in events]

    out_l = _apply_gain(left, _gain_track(len(left), left_spans, fade))
    out_r = _apply_gain(right, _gain_track(len(right), right_spans, fade))
    return {"events": public, "left": out_l, "right": out_r, "twist": twist}


def detect_debug(samples, sample_rate, tone_ms, threshold, fade=0):
    """Same as :func:`detect` plus per-channel spans and gain tracks.

    Intended for tests/tuning: ``events`` gain a ``left_span`` and
    ``right_span`` key and the result carries ``left_gain``/``right_gain``.
    """
    left = list(samples[0])
    right = list(samples[1]) if len(samples) > 1 else list(samples[0])
    sr = float(sample_rate)
    win = max(2, int(round(float(tone_ms) * sr / 1000.0)))
    hop = max(1, win // 4)
    thr = float(threshold)
    min_duration = max(1, int(round(win * DURATION_TOLERANCE)))
    lruns, rruns = _refined_runs(left, right, sr, win, hop, thr, min_duration)
    pairs = _pair_runs(lruns, rruns, win)
    events, left_spans, right_spans, twist = _build_events(pairs, thr)
    out = {"events": events,
           "left_gain": _gain_track(len(left), left_spans, fade),
           "right_gain": _gain_track(len(right), right_spans, fade),
           "twist": twist}
    return out


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 3:
        sys.stderr.write("usage: dtmf.py input.json output.json\n")
        return 2
    with open(argv[1], "r") as fh:
        data = json.load(fh)
    result = detect(data["samples"], data["sample_rate"], data["tone_ms"],
                    data["threshold"], data.get("fade", 0))
    with open(argv[2], "w") as fh:
        json.dump(result, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
