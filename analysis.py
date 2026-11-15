"""Delay analysis for a reference track and one or more target tracks.

Each stage targets a specific failure mode of naive cross-correlation on
multi-track material:

1. **Per-frame cross spectrum** (:func:`frame_xspec`).  Each frame gets a
   coherence weighted, PHAT whitened cross spectrum.  Coherence weighting
   (magnitude squared coherence from frequency smoothed auto/cross spectra)
   removes bins where only one track has energy, bins that are noise
   dominated, and bins dominated by a different (reverberant) arrival; PHAT
   removes the spectral tilt that lets a strong low frequency band decide the
   lag on its own.

2. **Coherent cross-frame aggregation** (:func:`aggregate_lag`).  The
   complex cross spectra of many frames are summed *before* the inverse FFT.
   Uncorrelated noise and reverb tails average towards zero while the
   coherent direct path adds in phase, so the resulting lag curve has a single
   clean peak even where no individual frame is trustworthy.  This is the
   primary estimate; it is equivalent to analysing one long observation
   window while keeping the time resolution of the frame grid.

   A short sliding window runs alongside it: the long window gives the low
   variance value, the short window locates a delay change to within a frame
   instead of smearing it.

3. **Peak ambiguity from low-frequency dominance.**  Material with a
   dominant, wandering fundamental (voice, bowed strings, hum) produces a lag
   curve with near-equal lobes one period apart.  A single frame regularly
   picks the wrong period (measured: ~15% of frames on such material), and
   PHAT whitening cannot fix that because the lobes are genuinely equal in
   height.  What does fix it is the aggregation of stage 2: the wrong-period
   choice is not consistent from frame to frame, so it averages out of the
   summed cross spectrum while the direct arrival reinforces.  The per-frame
   peak is kept only as ambiguity evidence for the confidence figure.  See
   the "low-frequency dominance" test.

4. **Segmentation and mapping** (:func:`segment_values` /
   :func:`map_pieces`).  The track is split into piecewise constant and
   linear (clock drift) pieces, each piece's delay is re-estimated from its
   own frames only (:func:`refine_pieces`), and every span that cannot be
   aligned -- insufficient overlap, silence, unidentifiable peak, or a
   required compensation beyond ``max_comp_ms`` -- is *reported* as unaligned
   with a reason code rather than silently interpolated.

Detectability is judged on the *aggregated* lag curve's peak-to-background
ratio.  That single statistic is what separates "there is a dominant arrival"
from "the argmax just returned the loudest noise lobe": unrelated material
stays near the ~12 dB noise floor, while alignable material is well above
it.  Frames below the threshold become unaligned instead of plausible-looking
wrong numbers.

Determinism
-----------
The analysis grid depends only on ``fs`` and the signal lengths, and frame
``k`` always covers the absolute sample range ``[k * hop, k * hop +
frame_len)``.  Per-frame spectra are therefore identical whether the input
arrives as whole files or as arbitrary chunks; :mod:`stream` implements the
same grid incrementally and asserts this frame for frame.

Sign convention
---------------
``lag`` is ``target_onset - reference_onset`` in milliseconds: a positive lag
means the target arrived **later** than the reference, and aligning it means
reading the target ``lag`` samples ahead (``y[n] = x[n + lag]``).  The mix and
every curve in the result use this single convention.
"""
from __future__ import annotations

import math

import dsp

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
DEFAULTS = {
    # analysis frame: target length in seconds, rounded to a power of two
    "frame_sec": 0.256,
    "min_frame": 1024,
    # lag search: at least min_search_ms, and always >= 2*max_lag_ms
    "min_search_ms": 60.0,
    "search_cap_ms": 400.0,
    # GCC shaping
    "phat_beta": 1.0,
    "msc_pow": 2.0,
    "msc_smooth_hz": 20.0,
    # analysis band
    "band_lo_hz": 60.0,
    "band_hi_ratio": 0.45,
    # candidate lobes (used for ambiguity reporting and change detection)
    "n_candidates": 5,
    "min_peak_sep_ms": 1.0,
    # cross-frame coherent aggregation: window over which complex cross
    # spectra are summed before the inverse FFT (the key robustness step)
    "agg_span_ms": 700.0,
    "change_span_ms": 160.0,
    # detectability gate on the aggregated lag curve
    "min_agg_db": 15.5,
    # physical gates
    "min_overlap": 0.35,
    "silence_db": -45.0,
    # ambiguity: a rival lobe this close to the main one is reported
    "ambiguity_ratio": 0.9,
    # confidence floor for the "reliable" flag
    "min_conf": 0.25,
    # segmentation
    "step_ms": 0.15,
    "min_drift_ppm": 20.0,
    "min_seg_frames": 2,
    "unreliable_min_ms": 20.0,
    # compensation limit in ms; None -> max_lag_ms
    "max_comp_ms": None,
    # compensation: cross-fade settings (only used at step/ramp boundaries)
    "chunk_hop_ms": 32.0,
    "fade_ms": 4.0,
}


def resolve_config(req):
    cfg = dict(DEFAULTS)
    for k, v in (req.get("options") or {}).items():
        if k in cfg:
            cfg[k] = v
    cfg["fs"] = int(req.get("fs") or 0)
    cfg["max_lag_ms"] = float(req.get("max_lag_ms", 20.0))
    if cfg["max_comp_ms"] is None:
        cfg["max_comp_ms"] = cfg["max_lag_ms"]
    return cfg


def frame_plan(fs, n_max, cfg):
    """Analysis grid.  Depends only on ``fs`` and the signal length."""
    target = int(round(cfg["frame_sec"] * fs))
    frame_len = dsp.next_pow2(max(int(cfg["min_frame"]), target))
    if n_max < frame_len:
        frame_len = dsp.next_pow2(max(128, n_max))
    hop = max(64, frame_len // 2)
    nfft = 2 * frame_len
    search_ms = max(2.0 * cfg["max_lag_ms"], cfg["min_search_ms"])
    search_ms = min(search_ms, cfg["search_cap_ms"])
    max_search = int(round(search_ms * fs / 1000.0))
    max_search = max(8, min(max_search, frame_len // 2 - 1))
    if n_max <= frame_len:
        starts = [0]
    else:
        starts = [i * hop for i in range((n_max - frame_len) // hop + 2)]
    return {
        "fs": fs,
        "frame_len": frame_len,
        "hop": hop,
        "nfft": nfft,
        "max_search": max_search,
        "search_ms": search_ms,
        "starts": starts,
        "n_frames": len(starts),
        "n_max": n_max,
        "center_ms": [(s + frame_len * 0.5) / float(fs) * 1000.0 for s in starts],
    }


def analysis_band(cfg, fs):
    hi = min(cfg["band_hi_ratio"] * 0.5 * fs, 0.45 * fs)
    lo = min(cfg["band_lo_hz"], 0.25 * hi)
    return lo, hi


def analysis_mask(plan, cfg):
    lo, hi = analysis_band(cfg, plan["fs"])
    return dsp.band_mask(plan["nfft"], plan["fs"], lo, hi,
                         taper_hz=max(25.0, 0.15 * lo))


def msc_smooth_bins(plan, cfg):
    width = int(round(cfg["msc_smooth_hz"] * plan["nfft"] / float(plan["fs"])))
    return max(3, width | 1)


# ---------------------------------------------------------------------------
# numeric helpers
# ---------------------------------------------------------------------------
def _moving(v, width, is_complex):
    n = len(v)
    if width <= 1 or n == 0:
        return list(v)
    half = width // 2
    zero = 0j if is_complex else 0.0
    pre = [zero] * (n + 1)
    s = zero
    for i in range(n):
        s += v[i]
        pre[i + 1] = s
    out = [zero] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = (pre[hi] - pre[lo]) / (hi - lo)
    return out


def smooth_real(v, width):
    return _moving(v, width, False)


def smooth_complex(v, width):
    return _moving(v, width, True)


def windowed_segment(x, start, length, win, nfft):
    """Windowed zero padded frame plus its energy and in-range sample count."""
    seg = [0.0] * nfft
    n = len(x)
    e = 0.0
    cnt = 0
    for i in range(length):
        idx = start + i
        if 0 <= idx < n:
            v = x[idx] * win[i]
            seg[i] = v
            e += v * v
            cnt += 1
    return seg, e, cnt


def overlap_samples(n, start, length):
    return max(0, min(n, start + length) - max(0, start))


# ---------------------------------------------------------------------------
# spectra and correlation curves
# ---------------------------------------------------------------------------
def _frame_spectra(ref_seg, tgt_seg, nfft):
    R = dsp.rfft(ref_seg, nfft)
    T = dsp.rfft(tgt_seg, nfft)
    nb = len(R)
    p1 = [0.0] * nb
    p2 = [0.0] * nb
    cr = [0j] * nb
    for k in range(nb):
        r = R[k]
        t = T[k]
        p1[k] = r.real * r.real + r.imag * r.imag
        p2[k] = t.real * t.real + t.imag * t.imag
        cr[k] = r * t.conjugate()
    return p1, p2, cr


def _coherence(p1, p2, cr, smw):
    """Frequency smoothed coherence weights plus the raw cross spectrum.

    Returns ``(gamma2, band_energy_product)`` where ``gamma2[k]`` is the
    magnitude squared coherence in [0, 1] (frequency-smoothed estimate).
    """
    s1 = smooth_real(p1, smw)
    s2 = smooth_real(p2, smw)
    sc = smooth_complex(cr, smw)
    nb = len(p1)
    gamma2 = [0.0] * nb
    e1 = 0.0
    e2 = 0.0
    for k in range(nb):
        den = s1[k] * s2[k]
        if den <= 1e-30:
            gamma2[k] = 0.0
            continue
        num = sc[k].real * sc[k].real + sc[k].imag * sc[k].imag
        g = num / den
        gamma2[k] = 1.0 if g > 1.0 else g
        e1 += p1[k]
        e2 += p2[k]
    return gamma2, sc, e1, e2


def _lag_window(corr, nfft, M):
    """+-M slice of a lag curve: window index i corresponds to lag i - M."""
    return corr[nfft - M:] + corr[:M + 1]


def peak_to_background(curve, i0, guard=None):
    """Strongest lobe level above the RMS of the rest of the lag curve (dB).

    Primary detectability statistic: "is there a dominant arrival at all?".
    Clean material scores 30..40 dB, unrelated noise about 12 dB.
    """
    n = len(curve)
    if guard is None:
        guard = max(8, n // 64)
    bg = 0.0
    cnt = 0
    for j in range(n):
        if abs(j - i0) > guard:
            bg += curve[j] * curve[j]
            cnt += 1
    if cnt == 0:
        return 0.0
    bg = math.sqrt(bg / cnt)
    return dsp.db((abs(curve[i0]) ** 2) / max(bg * bg, 1e-30))


def find_lobes(win, cfg, plan):
    """Distinct lobes of a correlation curve, strongest first.

    A lobe is a local maximum separated from every accepted stronger lobe by
    a real valley.  This separates two competing delay hypotheses (two
    arrivals, or two periods of a harmonic signal) from the ragged top of a
    single broad low frequency peak.
    """
    n = len(win)
    local = []
    for i in range(1, n - 1):
        v = win[i]
        if v > 0.0 and v >= win[i - 1] and v > win[i + 1]:
            local.append((v, i))
    if not local:
        i = max(range(n), key=lambda j: win[j])
        local.append((win[i], i))
    local.sort(key=lambda p: (-p[0], p[1]))
    min_sep = max(2, int(round(cfg["min_peak_sep_ms"] * plan["fs"] / 1000.0)))
    chosen = []
    for v, i in local:
        ok = True
        for v2, j in chosen:
            if abs(i - j) < min_sep:
                ok = False
                break
            lo = min(i, j)
            hi = max(i, j)
            if min(win[lo:hi + 1]) > 0.95 * min(v, v2):
                ok = False      # not separated by a genuine valley
                break
        if ok:
            chosen.append((v, i))
        if len(chosen) >= cfg["n_candidates"]:
            break
    return chosen


def refine_lag(corr, nfft, lag0, half=1.0, step=1.0 / 16.0, taps=16):
    """Sub-sample peak of a lag curve near ``lag0``.

    The curve is the inverse FFT of a band limited spectrum, so a
    Lanczos-sinc kernel interpolates it *exactly* within the band: this yields
    sub-sample (indeed sub-hundredth-sample on clean data) precision without
    any modelling of the waveform.  A coarse scan over ``lag0 +- half`` picks
    the right side of the lobe, a fine scan refines it and a parabolic finish
    on the fine grid lands the final estimate.
    """
    ev = lambda t: dsp.sinc_eval(corr, (-t) % nfft, taps)
    coarse = [lag0 + d for d in (-half, -0.5 * half, 0.0, 0.5 * half, half)]
    best_v = ev(coarse[0])
    best_t = coarse[0]
    for t in coarse[1:]:
        v = ev(t)
        if v > best_v:
            best_v = v
            best_t = t
    t = best_t
    for g in range(-6, 7):
        if g == 0:
            continue
        tt = best_t + g * step
        v = ev(tt)
        if v > best_v:
            best_v = v
            t = tt
    vl = ev(t - step)
    vr = ev(t + step)
    den = vl - 2.0 * best_v + vr
    delta = 0.0
    if den < -1e-30:
        delta = dsp.clamp(0.5 * (vl - vr) / den, -1.0, 1.0)
    fine = t + delta * step
    return fine, dsp.sinc_eval(corr, (-fine) % nfft, taps)


def confidence_of(piece, res, cfg):
    """Confidence in [0, 1] for an aligned piece.

    Built from three quantities that answer the question "how sure are we
    about this delay?":

    * ``rho`` -- the fraction of the two tracks that is common signal at the
      piece's delay (from the accumulated plain cross spectrum);
    * the piece's detectability (peak over background in the accumulated lag
      curve), mapped smoothly to 0..1 around the decision threshold;
    * the consistency of the frame estimates inside the piece (measured by
      their median absolute deviation).

    If the accumulated PHAT curve for the piece still has a rival lobe of
    nearly equal height, the delay could be one period off, so confidence is
    scaled down in proportion to how close that rival is.
    """
    a, b = piece["frames"]
    values = res["values"]
    idx = [k for k in range(a, b + 1)
           if 0 <= k < len(values) and values[k] is not None]
    if not idx:
        return 0.0
    rho = dsp.median([values[k]["rho"] for k in idx])
    db = dsp.median([values[k]["peak_db"] for k in idx])
    # smooth ramp: 0 at min_agg_db - 4 dB, 1 at min_agg_db + 6 dB
    lo = cfg["min_agg_db"] - 4.0
    hi = cfg["min_agg_db"] + 6.0
    db_term = dsp.clamp((db - lo) / (hi - lo), 0.0, 1.0)
    # rho ramp: 0 at 0.02, 1 at 0.35 (typical clean values are 0.7+)
    rho_term = dsp.clamp((rho - 0.02) / 0.33, 0.0, 1.0)
    lags = [values[k]["lag_ms"] for k in idx]
    spread = dsp.mad(lags) if len(lags) > 2 else 0.0
    spread_term = 1.0 / (1.0 + spread / max(cfg["step_ms"], 1e-9))
    base = 0.5 * (0.5 * db_term + 0.5 * rho_term) + 0.5 * spread_term
    base = dsp.clamp(base, 0.0, 1.0)
    # ambiguity of the accumulated PHAT curve over the piece
    specs = res["specs"]
    acc = None
    M = res["plan"]["max_search"]
    nfft = res["plan"]["nfft"]
    for k in idx:
        sp = specs[k]["spec"]
        if acc is None:
            acc = list(sp)
        else:
            for i in range(len(sp)):
                acc[i] += sp[i]
    if acc is not None:
        corr = dsp.irfft(acc, nfft)
        cw = _lag_window(corr, nfft, M)
        lobes = find_lobes(cw, cfg, res["plan"])
        if len(lobes) > 1 and lobes[0][0] > 0:
            ratio = lobes[1][0] / lobes[0][0]
            if ratio >= cfg["ambiguity_ratio"]:
                base *= (1.0 - ratio) / (1.0 - cfg["ambiguity_ratio"])
    return dsp.clamp(base, 0.0, 1.0)


# ---------------------------------------------------------------------------
# pipeline assembly
# ---------------------------------------------------------------------------
def frame_xspec(ref, tgt, start, plan, cfg, mask, smw, win):
    """Coherence weighted cross spectra for one frame, plus frame statistics.

    Returns a dict holding

    * ``spec``    -- the PHAT shaped, coherence weighted cross spectrum, used
      for lag peaks (sharp, and unbiased against spectral tilt);
    * ``cospec``  -- the plain coherence weighted cross spectrum, whose inverse
      at a *known* lag gives a true correlation coefficient (``rho``);
    * ``gamma2``  -- the per-bin magnitude squared coherence;
    * ``energy_norm`` -- normalisation making ``rho`` a coefficient in [0,1];
    * ``coh``, ``ref_db``, ``tgt_db``, ``overlap``, ``na``, ``nb`` -- the frame
      statistics the gates use.

    Keeping the spectra *un-inverted* is what lets :func:`aggregate_lag` sum
    many frames coherently before the inverse FFT, which is the key reason it
    is far more robust than any single frame.
    """
    nfft = plan["nfft"]
    frame_len = plan["frame_len"]
    a, ea, ca = windowed_segment(ref, start, frame_len, win, nfft)
    b, eb, cb = windowed_segment(tgt, start, frame_len, win, nfft)
    na = overlap_samples(len(ref), start, frame_len)
    nb = overlap_samples(len(tgt), start, frame_len)
    p1, p2, cr = _frame_spectra(a, b, nfft)
    gamma2, _sc, _e1, _e2 = _coherence(p1, p2, cr, smw)
    beta = cfg["phat_beta"]
    pow_ = cfg["msc_pow"]
    nbins = len(cr)
    spec = [0j] * nbins
    cospec = [0j] * nbins
    e1 = 0.0
    e2 = 0.0
    for k in range(nbins):
        m = mask[k]
        if m <= 0.0:
            continue
        w = (gamma2[k] ** pow_) * m
        raw = cr[k]
        cospec[k] = raw * w
        mag = math.sqrt(raw.real * raw.real + raw.imag * raw.imag)
        if mag > 1e-24:
            if beta >= 1.0:
                spec[k] = raw / mag * w
            else:
                spec[k] = raw * (mag ** (beta - 1.0)) * w
        e1 += p1[k] * m
        e2 += p2[k] * m
    ref_rms2 = ea / ca if ca else 0.0
    tgt_rms2 = eb / cb if cb else 0.0
    return {
        "start": start,
        "spec": spec,
        "cospec": cospec,
        "gamma2": gamma2,
        "energy_norm": 2.0 * math.sqrt(max(e1 * e2, 1e-30)) / nfft,
        "coh": (sum(gamma2[k] * mask[k] for k in range(nbins)) /
                max(1e-9, sum(mask))),
        "ref_db": dsp.db(ref_rms2),
        "tgt_db": dsp.db(tgt_rms2),
        "na": na,
        "nb": nb,
        "overlap": min(na, nb) / float(na) if na else 0.0,
    }


def aggregate_lag(specs, plan, cfg, guess=None, half=None):
    """Lag from coherently accumulated frame cross spectra.

    Summing the *complex* shaped cross spectra of many frames before the
    inverse FFT is the key robustness step of this design.  Uncorrelated noise
    and reverb tails average towards zero while the coherent direct path adds
    in phase, so the resulting lag curve has a single, very clean peak even
    when no individual frame is trustworthy -- equivalent to analysing one
    long window, but with the time resolution of the frame grid.

    ``guess``/``half`` restrict the peak search (used for refinement after
    segmentation).  Returns ``(lag_samples, peak_value, peak_db, n_used)``.
    """
    nfft = plan["nfft"]
    M = plan["max_search"]
    acc = None
    n_used = 0
    for s in specs:
        sp = s["spec"]
        if acc is None:
            acc = list(sp)
        else:
            for i in range(len(sp)):
                acc[i] += sp[i]
        n_used += 1
    if acc is None or n_used == 0:
        return None
    corr = dsp.irfft(acc, nfft)
    if guess is None:
        w = _lag_window(corr, nfft, M)
        i0 = max(range(len(w)), key=lambda i: w[i])
        lag0 = float(M - i0)
    else:
        lag0 = float(guess)
    lag, pk = refine_lag(corr, nfft, lag0,
                         half=(1.0 if half is None else half))
    # detectability of the accumulated peak
    cw = _lag_window(corr, nfft, M)
    i0 = min(range(len(cw)), key=lambda i: abs((M - i) - lag))
    pdb = peak_to_background(cw, i0)
    return lag, pk, pdb, n_used


def rho_at(specs, norm_sum, lag, plan):
    """Normalised correlation at a known lag from accumulated plain spectra.

    ``rho`` is the fraction of the two tracks that is common signal at that
    delay.  Because it is evaluated at an *already determined* lag, it does not
    suffer from the peak-selection ambiguity; it only has to answer "how much
    of this is real?" -- which makes it a robust reliability gate.
    """
    nfft = plan["nfft"]
    acc = None
    for s in specs:
        cs = s["cospec"]
        if acc is None:
            acc = list(cs)
        else:
            for i in range(len(cs)):
                acc[i] += cs[i]
    if acc is None or norm_sum <= 0.0:
        return 0.0
    corr = dsp.irfft(acc, nfft)
    v = dsp.sinc_eval(corr, (-lag) % nfft, 16)
    return dsp.clamp(v / norm_sum, 0.0, 0.999999)


def analyze_pair(ref, tgt, cfg):
    """Analyse one (reference, target) pair.

    Pipeline
    --------
    1. Per-frame shaped cross spectra (:func:`frame_xspec`), computed on the
       same absolute frame grid for every chunk size.
    2. Two sliding coherent aggregates (:func:`aggregate_lag`): a long window
       for the low-variance delay value, and a short window whose median track
       reacts fast enough to *locate* a delay step or clock restart.
    3. The per-frame PHAT peak is recorded as one extra piece of evidence and
       also gives ``rho`` (the common-signal fraction) for the confidence
       figure.
    4. Segmentation splits the short-window track into constant / drift pieces;
       each piece's delay is then re-aggregated over that piece's own frames
       (:func:`refine_pieces`) so the reported value is not smeared across a
       boundary, and unaligned spans keep an explicit reason.
    """
    fs = cfg["fs"]
    plan = frame_plan(fs, max(len(ref), len(tgt)), cfg)
    mask = analysis_mask(plan, cfg)
    smw = msc_smooth_bins(plan, cfg)
    win = dsp.hann(plan["frame_len"])
    n = plan["n_frames"]

    specs = []
    for s in plan["starts"]:
        specs.append(frame_xspec(ref, tgt, s, plan, cfg, mask, smw, win))

    usable = []
    unusable_reason = [None] * n
    for k, s in enumerate(specs):
        if s["overlap"] < cfg["min_overlap"]:
            unusable_reason[k] = "insufficient_overlap"
        elif s["ref_db"] < cfg["silence_db"]:
            unusable_reason[k] = "reference_silent"
        elif s["tgt_db"] < cfg["silence_db"]:
            unusable_reason[k] = "track_silent"
        usable.append(unusable_reason[k] is None)

    # ---- stage 2: sliding coherent aggregation ---------------------------
    # Two windows are used, each solving a different problem:
    #  * a LONG coherent window gives the low-variance delay estimate -- this
    #    is what makes hugely reverberant or noisy material still usable;
    #  * a SHORT window gives a delay estimate that reacts quickly to a change,
    #    which is needed to *locate* a step or a clock restart to within a
    #    frame instead of smearing it across the long window.
    # Segmentation uses the short-window track to decide where the delay
    # changes and the long-window track for the values, which is why a delay
    # step is reported at the right instant without the long window's
    # interpolation across the boundary.
    def window_track(span_frames, combine):
        out_lag = [None] * n
        out_db = [0.0] * n
        if span_frames <= 1:
            wins = [[k] for k in range(n)]
        else:
            half = span_frames // 2
            wins = [list(range(max(0, k - half), min(n, k + half + 1)))
                    for k in range(n)]
        per_window = []
        for k in range(n):
            idx = [j for j in wins[k] if usable[j]]
            if not idx:
                per_window.append(None)
                continue
            g = aggregate_lag([specs[j] for j in idx], plan, cfg)
            per_window.append(g)
        for k in range(n):
            cands = []
            half_w = (span_frames // 2) if span_frames > 1 else 0
            for j in range(max(0, k - half_w), min(n, k + half_w + 1)):
                g = per_window[j]
                if g is not None:
                    cands.append(g[0])
            if not cands:
                continue
            if combine == "median":
                out_lag[k] = dsp.median(cands)
            else:
                g = per_window[k]
                if g is not None:
                    out_lag[k] = g[0]
            g = per_window[k]
            if g is not None:
                out_db[k] = g[2]
        return out_lag, out_db

    long_span = max(1, int(round(cfg["agg_span_ms"] /
                                 (plan["hop"] / float(fs) * 1000.0))))
    short_span = max(1, int(round(cfg["change_span_ms"] /
                                  (plan["hop"] / float(fs) * 1000.0))))
    agg_lag, agg_db = window_track(long_span, "mean")
    fast_lag, _fast_db = window_track(max(1, short_span), "median")

    # ---- stage 3: per-frame peaks as change evidence ---------------------
    raw_lag = [None] * n
    raw_db = [0.0] * n
    raw_rho = [0.0] * n
    for k in range(n):
        s = specs[k]
        if not usable[k]:
            continue
        corr = dsp.irfft(s["spec"], plan["nfft"])
        M = plan["max_search"]
        cw = _lag_window(corr, plan["nfft"], M)
        i0 = max(range(len(cw)), key=lambda i: cw[i])
        lag, _pk = refine_lag(corr, plan["nfft"], float(M - i0))
        raw_lag[k] = lag
        raw_db[k] = peak_to_background(cw, i0)
        if s["energy_norm"] > 0:
            plain = dsp.irfft(s["cospec"], plan["nfft"])
            pv = dsp.sinc_eval(plain, (-lag) % plan["nfft"], 16)
            raw_rho[k] = dsp.clamp(pv / s["energy_norm"], 0.0, 0.999999)

    values, flags = build_values(agg_lag, agg_db, fast_lag, raw_lag, raw_rho,
                                 usable, unusable_reason, plan, cfg)
    segments = segment_values(values, plan, cfg, unusable_reason)
    pieces, total_ms = map_pieces(segments, values, plan, cfg)
    refine_pieces(pieces, specs, plan, cfg)
    res = {
        "plan": plan,
        "specs": specs,
        "values": values,
    }
    for p in pieces:
        p["conf"] = 0.0 if p["kind"] == "gap" else confidence_of(p, res, cfg)
    return {
        "plan": plan,
        "specs": specs,
        "usable": usable,
        "agg_lag": agg_lag,
        "agg_db": agg_db,
        "fast_lag": fast_lag,
        "raw_lag": raw_lag,
        "raw_db": raw_db,
        "raw_rho": raw_rho,
        "values": values,
        "flags": flags,
        "segments": segments,
        "pieces": pieces,
        "total_ms": total_ms,
        "cfg": cfg,
    }


# ---------------------------------------------------------------------------
# stage 3b: combine aggregate and per-frame evidence
# ---------------------------------------------------------------------------
def build_values(agg_lag, agg_db, fast_lag, raw_lag, raw_rho, usable,
                 unusable_reason, plan, cfg):
    """Combine the long-window, short-window and single-frame estimates.

    * ``agg_lag``  -- long coherent window: minimum variance, but it
      interpolates across a delay change;
    * ``fast_lag`` -- median of short-window estimates: reacts within a frame,
      so it *locates* changes (it is also much less noisy than a single
      frame, which is what makes it usable at low SNR);
    * ``raw_lag``  -- single frame: used only as a last resort when both
      aggregated tracks are missing for this frame.

    ``lag_ms`` per frame carries the fast track (for segmentation, i.e. for
    finding the boundaries) and the piece values are re-estimated afterwards
    from the long window (:func:`refine_pieces`).
    """
    fs = plan["fs"]
    n = len(agg_lag)
    values = [None] * n
    flags = [[] for _ in range(n)]
    for k in range(n):
        if not usable[k]:
            flags[k].append(unusable_reason[k] or "unusable")
            continue
        a = agg_lag[k]
        f = fast_lag[k]
        lag = f if f is not None else a
        source = "fast" if f is not None else "aggregate"
        if lag is None:
            lag = raw_lag[k]
            source = "frame"
        if lag is None:
            flags[k].append("no_estimate")
            continue
        # Detectability gate.  A lag curve whose peak is not clearly above the
        # background carries no usable delay, whatever value the argmax
        # returned -- this is what makes an unidentifiable stretch (extreme
        # reverb, unresolved noise, unrelated tracks) report as unaligned
        # instead of as a plausible-looking wrong number.
        pdb = agg_db[k]
        if pdb < cfg["min_agg_db"]:
            flags[k].append("low_detectability")
            continue
        values[k] = {
            "lag": lag,
            "lag_ms": lag / float(fs) * 1000.0,
            "agg_lag": a,
            "agg_db": pdb,
            "peak_db": pdb,
            "rho": raw_rho[k],
            "source": source,
        }
    return values, flags


def refine_pieces(pieces, specs, plan, cfg):
    """Re-estimate each aligned piece's delay from its own frames only.

    Using only the frames belonging to the piece avoids the interpolation the
    long sliding window performs across a boundary, so the reported value is
    the delay of *that* stretch.  A ramp piece is fitted by least squares on
    short sub-window estimates so a clock drift is followed rather than
    averaged out.
    """
    fs = plan["fs"]
    nfft = plan["nfft"]
    M = plan["max_search"]
    for p in pieces:
        if p["kind"] == "gap":
            continue
        a, b = p["frames"]
        idx = [k for k in range(a, b + 1) if 0 <= k < len(specs)]
        if not idx:
            continue
        if p["kind"] == "const":
            g = aggregate_lag([specs[k] for k in idx], plan, cfg)
            if g is None:
                continue
            lag, _pk, pdb, _nu = g
            if pdb < cfg["min_agg_db"]:
                continue
            p["params"] = [lag / float(fs) * 1000.0]
            p["peak_db"] = pdb
            p["rho"] = rho_at([specs[k] for k in idx],
                              sum(s["energy_norm"] for s in
                                  (specs[k] for k in idx)),
                              lag, plan)
        else:
            # ramp: fit a line on sub-window estimates across the piece
            span = max(1, int(round(cfg["change_span_ms"] /
                                    (plan["hop"] / float(fs) * 1000.0))))
            xs = []
            ys = []
            for k in idx:
                lo = max(a, k - span // 2)
                hi = min(b, k + span // 2)
                sub = [specs[j] for j in range(lo, hi + 1)]
                if not sub:
                    continue
                g = aggregate_lag(sub, plan, cfg)
                if g is None:
                    continue
                xs.append((k * plan["hop"] + plan["frame_len"] * 0.5) /
                          float(fs) * 1000.0)
                ys.append(g[0] / float(fs) * 1000.0)
            if len(xs) < 3:
                g = aggregate_lag([specs[k] for k in idx], plan, cfg)
                if g is not None:
                    p["kind"] = "const"
                    p["params"] = [g[0] / float(fs) * 1000.0]
                continue
            xm = sum(xs) / len(xs)
            ym = sum(ys) / len(ys)
            sxx = sum((x - xm) ** 2 for x in xs)
            if sxx <= 0:
                continue
            sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            slope = sxy / sxx
            p["params"] = [ym - slope * xm, slope]


# ---------------------------------------------------------------------------
# stage 4: segmentation and mapping
# ---------------------------------------------------------------------------
def _fit_const(idx, lags):
    return dsp.median([lags[i] for i in idx])


def _fit_line(idx, lags, centers):
    m = len(idx)
    if m < 2:
        return None
    xs = [centers[i] for i in idx]
    ys = [lags[i] for i in idx]
    xm = sum(xs) / m
    ym = sum(ys) / m
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = ym - b * xm
    return a, b


def _resid(kind, params, idx, lags, centers):
    if kind == "const":
        return [lags[i] - params[0] for i in idx]
    return [lags[i] - (params[0] + params[1] * centers[i]) for i in idx]


def segment_values(values, plan, cfg, unusable_reason=None):
    """Split the frame track into constant / drift / gap pieces.

    The greedy splitter accepts a longer piece whenever a const *or* linear
    fit keeps every residual inside ``step_ms``; it prefers a linear model
    only when the implied drift is meaningful (``min_drift_ppm``), so noise in
    the frame estimates is not turned into a fake clock offset.
    """
    fs = plan["fs"]
    n = len(values)
    hop = plan["hop"]
    centers = [(k * hop + plan["frame_len"] * 0.5) / float(fs) * 1000.0
               for k in range(n)]
    lags = [values[k]["lag_ms"] if values[k] is not None else None
            for k in range(n)]
    tol = cfg["step_ms"]
    min_frames = max(1, int(cfg["min_seg_frames"]))

    def t0(k):
        return (k * hop) / float(fs) * 1000.0

    def t1(k):
        return ((k + 1) * hop) / float(fs) * 1000.0

    def best_fit(idx):
        c = _fit_const(idx, lags)
        cand = [("const", [c], _resid("const", [c], idx, lags, centers))]
        if len(idx) >= max(3, min_frames):
            ln = _fit_line(idx, lags, centers)
            if ln is not None:
                ppm = ln[1] * 1e6
                if abs(ppm) >= cfg["min_drift_ppm"]:
                    cand.append(("line", list(ln),
                                 _resid("line", ln, idx, lags, centers)))
        best = None
        for kind, params, res in cand:
            worst = max(abs(r) for r in res) if res else 0.0
            rms = math.sqrt(sum(r * r for r in res) / len(res)) if res else 0.0
            score = (worst, rms)
            if best is None or score < best[0]:
                best = (score, kind, params)
        return best[1], best[2], best[0]

    segments = []
    k = 0
    while k < n:
        if lags[k] is None:
            j = k
            while j < n and lags[j] is None:
                j += 1
            reasons = set(unusable_reason[i] for i in range(k, j)
                          if unusable_reason and unusable_reason[i])
            reason = reasons.pop() if len(reasons) == 1 else "unaligned"
            segments.append({"kind": "gap", "t0_ms": t0(k), "t1_ms": t1(j - 1),
                             "params": [], "frames": [k, j - 1],
                             "reason": reason, "conf": 0.0})
            k = j
            continue
        # extend while the fit stays inside tolerance
        end = k
        last_kind, last_params = "const", [_fit_const([k], lags)]
        while end + 1 < n and lags[end + 1] is not None:
            trial = list(range(k, end + 2))
            kind, params, worst = best_fit(trial)
            if worst[0] > tol:
                break
            end += 1
            last_kind, last_params = kind, params
        if end == k and last_kind == "const":
            last_params = [_fit_const([k], lags)]
        segments.append({"kind": last_kind, "t0_ms": t0(k), "t1_ms": t1(end),
                         "params": last_params, "frames": [k, end],
                         "reason": None, "conf": 0.0})
        k = end + 1
    # A change between two frames happens somewhere inside the interval that
    # separates them, and that interval is *overlapped* by both frames.  Split
    # it at the midpoint of the two frames' centres, which halves the worst
    # case boundary error compared with using the frame edge.
    for i in range(len(segments) - 1):
        left, right = segments[i], segments[i + 1]
        if left["kind"] == "gap" or right["kind"] == "gap":
            continue
        boundary = 0.5 * (centers[left["frames"][1]] +
                          centers[right["frames"][0]])
        left["t1_ms"] = boundary
        right["t0_ms"] = boundary
    return segments


def map_pieces(segments, values, plan, cfg):
    """Convert segments into a compensation map with explicit unaligned spans."""
    fs = plan["fs"]
    hop = plan["hop"]
    max_comp = cfg["max_comp_ms"]
    total_ms = (len(values) * hop) / float(fs) * 1000.0

    def t0(k):
        return (k * hop) / float(fs) * 1000.0

    def t1(k):
        return min(total_ms, ((k + 1) * hop) / float(fs) * 1000.0)

    def lag_of(seg, k):
        if values[k] is None:
            return None
        if seg["kind"] == "const":
            return seg["params"][0]
        c = (k * hop + plan["frame_len"] * 0.5) / float(fs) * 1000.0
        return seg["params"][0] + seg["params"][1] * c

    pieces = []
    for seg in segments:
        if seg["kind"] == "gap":
            pieces.append(dict(seg))
            continue
        a, b = seg["frames"]
        run_start = None
        run_reason = None
        for k in range(a, b + 1):
            lag = lag_of(seg, k)
            if lag is None:
                bad, reason = True, "unaligned"
            elif abs(lag) > max_comp:
                bad, reason = True, "beyond_max_comp"
            else:
                bad, reason = False, None
            if bad:
                if run_start is None:
                    run_start, run_reason = k, reason
                elif run_reason != reason:
                    pieces.append({"kind": "gap", "t0_ms": t0(run_start),
                                   "t1_ms": t0(k), "params": [], "frames":
                                   [run_start, k - 1], "reason": run_reason,
                                   "conf": 0.0})
                    run_start, run_reason = k, reason
            elif run_start is not None:
                pieces.append({"kind": "gap", "t0_ms": t0(run_start),
                               "t1_ms": t0(k), "params": [],
                               "frames": [run_start, k - 1],
                               "reason": run_reason, "conf": 0.0})
                run_start, run_reason = None, None
        if run_start is not None:
            pieces.append({"kind": "gap", "t0_ms": t0(run_start),
                           "t1_ms": t1(b), "params": [],
                           "frames": [run_start, b], "reason": run_reason,
                           "conf": 0.0})
        else:
            first, last = a, b
            while first <= last and values[first] is None:
                first += 1
            while last >= first and values[last] is None:
                last -= 1
            if first <= last:
                p = dict(seg)
                # keep the segment's own boundaries: they were placed at the
                # midpoint between the frames straddling a change, which is a
                # better estimate of the instant than the raw frame grid.
                p["t0_ms"] = seg["t0_ms"] if first == a else t0(first)
                p["t1_ms"] = seg["t1_ms"] if last == b else t1(last)
                p["frames"] = [first, last]
                pieces.append(p)
    pieces.sort(key=lambda p: (p["t0_ms"], p["t1_ms"]))
    return pieces, total_ms
