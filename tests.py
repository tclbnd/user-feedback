#!/usr/bin/env python3
"""Acceptance tests: ``python3 tests.py``.

Everything runs from the standard library; the synthetic material carries
known ground truth so the assertions are measurements, not smoke tests.

Covered:
  * sub-sample accuracy of the estimator (<= 0.5 ms, typically << 0.1 ms)
    on clean, noisy, reverberant and low-frequency-dominated material;
  * comb-notch reduction of the mix (>= 12 dB) versus no compensation;
  * segmented verification of a clock restart and of a two-delay take;
  * unaligned-span reporting for silence / unrelated / unequal lengths;
  * chunk-size invariance of the whole analysis;
  * track-order invariance of the summary digest;
  * determinism (identical bytes on repeat runs).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile

import analysis
import align
import compensate
import dsp
import stream
import synth

FS = 16000
FAIL = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (status, name, ("  " + detail) if detail else ""))
    if not cond:
        FAIL.append(name)
    return cond


def estimate(ref, tgt, cfg):
    return analysis.analyze_pair(ref, tgt, cfg)


def const_lag_ms(res):
    """Delay of the longest constant piece (or time-weighted line value)."""
    pieces = [p for p in res["pieces"] if p["kind"] != "gap"]
    if not pieces:
        return None
    consts = [p for p in pieces if p["kind"] == "const"]
    if consts:
        best = max(consts, key=lambda p: p["t1_ms"] - p["t0_ms"])
        return best["params"][0]
    lines = [p for p in pieces if p["kind"] == "line"]
    if lines:
        best = max(lines, key=lambda p: p["t1_ms"] - p["t0_ms"])
        return best["params"][0] + best["params"][1] * best["t0_ms"]
    return None


def pair_notch(ref, tgt, res, cfg):
    """(raw notch depth, aligned notch depth, improvement) for one pair."""
    y, _app = compensate.compensate_track(tgt, res["pieces"], FS, cfg)
    lo = min(len(y), len(ref))
    d_raw, _ = compensate.comb_notch_db([ref[:lo], tgt[:lo]], FS)
    d_ali, _ = compensate.comb_notch_db([ref[:lo], y[:lo]], FS)
    return d_raw, d_ali, d_raw - d_ali


# ---------------------------------------------------------------------------
# 1. estimation accuracy
# ---------------------------------------------------------------------------
def test_accuracy():
    print("\n1. Sub-sample delay accuracy (target <= 0.5 ms)")
    n = int(6.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=20))
    cases = [
        ("clean, 12.5 ms", dict(delay_ms=12.5, snr_db=60.0)),
        ("noisy 5 dB", dict(delay_ms=12.5, snr_db=5.0)),
        ("noisy 0 dB", dict(delay_ms=12.5, snr_db=0.0)),
        ("fractional 7.25 ms", dict(delay_ms=7.25, snr_db=40.0)),
        ("negative -6.25 ms", dict(delay_ms=-6.25, snr_db=40.0)),
        ("reverb 0.35 s", dict(delay_ms=12.5, snr_db=40.0)),
    ]
    for label, kw in cases:
        ref, targets = synth.multi_track(
            n, FS, [dict(name="t", delay_ms=kw["delay_ms"], snr_db=kw["snr_db"])],
            seed=3, rt60=0.35 if "reverb" in label else None,
            ref_drr_db=0.0, tgt_drr_db=-3.0)
        tgt = targets[0]["signal"]
        res = estimate(ref, tgt, cfg)
        est = const_lag_ms(res)
        truth = kw["delay_ms"]
        err = abs((est or 0.0) - truth)
        check("%s: |err| <= 0.5 ms" % label, err <= 0.5,
              "est %.4f ms, err %.4f ms" % (est or 0.0, err))


def test_low_freq_dominance():
    print("\n2. Low-frequency-dominated material (period ambiguity)")
    n = int(6.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=25))
    # a strong, slowly gliding fundamental: its lag curve has a comb of
    # near-equal lobes one period apart.  A naive argmax lands on the wrong
    # period; the aggregate + segmentation must not.
    rng = __import__("random").Random(11)
    src = [0.0] * n
    for i in range(n):
        t = i / float(FS)
        f0 = 95.0 + 25.0 * math.sin(2 * math.pi * 0.7 * t)
        v = 0.0
        for h in range(1, 7):
            v += math.sin(2 * math.pi * f0 * h * t + 0.4 * h) / h
        src[i] = v * (0.5 + 0.5 * math.sin(2 * math.pi * 1.1 * t))
    src = synth.scale_to_rms(src, 0.25)
    ref = list(src)
    tgt = synth.delay_fractional(src, 11.75 * FS / 1000.0)
    res = estimate(ref, tgt, cfg)
    est = const_lag_ms(res)
    check("gliding fundamental: |err| <= 0.5 ms", abs((est or 0) - 11.75) <= 0.5,
          "est %.4f ms (truth 11.75)" % (est or 0.0))


# ---------------------------------------------------------------------------
# 3. comb-notch reduction
# ---------------------------------------------------------------------------
def residual_over_interval(ref, y, win=8192, search=400):
    """Residual delay measured on consecutive overlapping windows.

    This is the "covered interval" form of the accuracy criterion: instead of
    checking one estimate, every window of the aligned output is compared with
    the reference, so any local drift or boundary artefact is caught.
    """
    errs = []
    start = 2048
    while start + win + 2048 < min(len(y), len(ref)):
        a = ref[start:start + win]
        b = y[start:start + win]
        n = dsp.next_pow2(2 * win)
        A = dsp.rfft(a, n)
        B = dsp.rfft(b, n)
        c = dsp.irfft([A[k] * B[k].conjugate() for k in range(n // 2 + 1)], n)
        w = c[n - search:] + c[:search + 1]
        i0 = max(range(len(w)), key=lambda i: w[i])
        lag, _pk = analysis.refine_lag(c, n, float(search - i0),
                                       half=0.5, step=1.0 / 32.0)
        errs.append(abs(lag) / FS * 1000.0)
        start += win // 2
    return errs


def test_interval_coverage():
    print("\n1b. Residual delay over the covered interval")
    n = int(8.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=25))
    for label, snr, rt in [("clean", 60.0, None), ("noisy 0 dB", 0.0, None),
                           ("reverb 0.4 s", 40.0, 0.4)]:
        ref, targets = synth.multi_track(
            n, FS, [dict(name="t", delay_ms=13.37, snr_db=snr)],
            seed=4, rt60=rt, ref_drr_db=0.0, tgt_drr_db=-3.0)
        tgt = targets[0]["signal"]
        res = analysis.analyze_pair(ref, tgt, cfg)
        y, _ = compensate.compensate_track(tgt, res["pieces"], FS, cfg)
        errs = residual_over_interval(ref, y)
        errs.sort()
        worst = errs[-1] if errs else 9.9
        check("%s: every window |residual| <= 0.5 ms" % label, worst <= 0.5,
              "%d windows, median %.5f ms, max %.5f ms" % (
                  len(errs), errs[len(errs) // 2] if errs else 0.0, worst))


def test_comb():
    print("\n3. Comb-notch reduction of the mix (target >= 12 dB)")
    n = int(5.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=20))
    cases = [
        ("clean, 12.5 ms", dict(delay_ms=12.5, snr_db=40.0), None, 0.0),
        ("clean, 0.7 ms", dict(delay_ms=0.7, snr_db=40.0), None, 0.0),
        ("clean, 6 ms", dict(delay_ms=6.0, snr_db=40.0), None, 0.0),
        ("noisy 20 dB", dict(delay_ms=12.5, snr_db=20.0), None, 0.0),
        ("reverb 0.4 s", dict(delay_ms=12.5, snr_db=40.0), 0.4, -3.0),
    ]
    for label, kw, rt, drr in cases:
        ref, targets = synth.multi_track(
            n, FS, [dict(name="t", delay_ms=kw["delay_ms"], snr_db=kw["snr_db"])],
            seed=3, rt60=rt, ref_drr_db=drr, tgt_drr_db=drr)
        tgt = targets[0]["signal"]
        res = estimate(ref, tgt, cfg)
        d_raw, d_ali, imp = pair_notch(ref, tgt, res, cfg)
        check("%s: improvement >= 12 dB" % label, imp >= 12.0,
              "raw %.1f dB -> aligned %.1f dB, improve %.1f dB" % (d_raw, d_ali, imp))


# ---------------------------------------------------------------------------
# 4. segmented verification (clock restart, two delays)
# ---------------------------------------------------------------------------
def test_segments():
    print("\n4. Segmented verification (clock restart / two delays)")
    n = int(6.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=20))
    # (a) clock restart: delay 6.0 ms then 14.0 ms at t = 3 s
    ref, targets = synth.multi_track(
        n, FS, [dict(name="t", two_segment=(6.0, 14.0, 3.0), snr_db=30.0)],
        seed=3, rt60=0.2, ref_drr_db=0.0, tgt_drr_db=-3.0)
    res = estimate(ref, targets[0]["signal"], cfg)
    consts = [p for p in res["pieces"] if p["kind"] == "const"]
    ok = len(consts) >= 2
    if ok:
        first = min(consts, key=lambda p: p["t0_ms"])
        second = max(consts, key=lambda p: p["t1_ms"])
        ok = (abs(first["params"][0] - 6.0) <= 0.5 and
              abs(second["params"][0] - 14.0) <= 0.5)
        switch = second["t0_ms"]
        # the change is located between the two frames that straddle it, so
        # the achievable boundary resolution is half a hop (~64 ms here)
        ok = ok and abs(switch - 3000.0) <= 100.0
        check("clock restart: two pieces 6/14 ms", ok,
              "%.2f ms then %.2f ms, switch at %.0f ms" % (
                  first["params"][0], second["params"][0], switch))
    else:
        check("clock restart: two pieces 6/14 ms", False,
              "found %d constant pieces" % len(consts))

    # (b) a second take with two different delays, different switch time
    ref2, targets2 = synth.multi_track(
        n, FS, [dict(name="t", two_segment=(-2.5, 9.5, 1.8), snr_db=30.0)],
        seed=9, rt60=0.2, ref_drr_db=0.0, tgt_drr_db=-3.0)
    res2 = estimate(ref2, targets2[0]["signal"], cfg)
    consts2 = [p for p in res2["pieces"] if p["kind"] == "const"]
    ok2 = len(consts2) >= 2
    if ok2:
        f2 = min(consts2, key=lambda p: p["t0_ms"])
        s2 = max(consts2, key=lambda p: p["t1_ms"])
        ok2 = (abs(f2["params"][0] + 2.5) <= 0.5 and
               abs(s2["params"][0] - 9.5) <= 0.5 and
               abs(s2["t0_ms"] - 1800.0) <= 100.0)
        check("second take: -2.5 / 9.5 ms at 1.8 s", ok2,
              "%.2f ms then %.2f ms, switch at %.0f ms" % (
                  f2["params"][0], s2["params"][0], s2["t0_ms"]))
    else:
        check("second take: -2.5 / 9.5 ms at 1.8 s", False,
              "found %d constant pieces" % len(consts2))

    # (c) clock drift: a constant offset plus a linear ramp
    ref3, targets3 = synth.multi_track(
        n, FS, [dict(name="t", delay_ms=4.0, drift_ppm=250.0, snr_db=40.0)],
        seed=3)
    res3 = estimate(ref3, targets3[0]["signal"], cfg)
    lines = [p for p in res3["pieces"] if p["kind"] == "line"]
    if lines:
        ppm = lines[0]["params"][1] * 1e6
        check("clock drift: 250 ppm detected", abs(ppm - 250.0) <= 40.0,
              "%.1f ppm" % ppm)
    else:
        check("clock drift: 250 ppm detected", False, "no line piece found")


# ---------------------------------------------------------------------------
# 5. unaligned spans
# ---------------------------------------------------------------------------
def test_unaligned():
    print("\n5. Unaligned spans are reported, not interpolated")
    n = int(6.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=20))

    # (a) unequal lengths: the shorter track simply has no data at the end
    ref, targets = synth.multi_track(
        n, FS, [dict(name="t", delay_ms=5.0, length_ratio=0.6, snr_db=40.0)],
        seed=3)
    res = estimate(ref, targets[0]["signal"], cfg)
    gaps = [p for p in res["pieces"] if p["kind"] == "gap"]
    tail = any(g["t1_ms"] >= 0.55 * 6000.0 for g in gaps)
    check("short track: trailing span unaligned", tail,
          "%d gap pieces, delay est %s" % (
              len(gaps), round(const_lag_ms(res) or 0.0, 3)))
    aligned_conf = [p["conf"] for p in res["pieces"] if p["kind"] != "gap"]
    check("short track: aligned part is confident",
          max(aligned_conf) >= cfg["min_conf"] if aligned_conf else False,
          "max conf %.3f" % (max(aligned_conf) if aligned_conf else 0.0))

    # (b) unrelated track: nothing may be reported as aligned
    rng = __import__("random").Random(17)
    ref2, _ = synth.multi_track(n, FS, [dict(name="x", delay_ms=0.0)], seed=7)
    noise = [rng.gauss(0.0, 0.08) for _ in range(n)]
    res2 = estimate(ref2, noise, cfg)
    aligned = [p for p in res2["pieces"] if p["kind"] != "gap"]
    check("unrelated track: no aligned span", len(aligned) == 0,
          "%d aligned pieces" % len(aligned))

    # (c) silence at the start of the target
    ref3, targets3 = synth.multi_track(
        n, FS, [dict(name="t", delay_ms=6.0, snr_db=40.0)], seed=3)
    tgt = targets3[0]["signal"]
    for i in range(int(1.2 * FS)):
        tgt[i] = 0.0
    res3 = estimate(ref3, tgt, cfg)
    gaps3 = [p for p in res3["pieces"] if p["kind"] == "gap"]
    early = any(g["t0_ms"] <= 100.0 for g in gaps3)
    check("leading silence: reported unaligned", early,
          "%d gap pieces, first at %.0f ms" % (
              len(gaps3), gaps3[0]["t0_ms"] if gaps3 else -1))


# ---------------------------------------------------------------------------
# 6. chunk invariance and determinism
# ---------------------------------------------------------------------------
def test_chunk_invariance():
    print("\n6. Chunk-size invariance")
    n = int(3.0 * FS)
    cfg = analysis.resolve_config(dict(fs=FS, max_lag_ms=20))
    ref, targets = synth.multi_track(n, FS, [dict(name="t", delay_ms=8.75, snr_db=15.0)],
                                     seed=5, rt60=0.3)
    tgt = targets[0]["signal"]
    sizes = [512, 1024, 3333, 8192, 65536, len(ref) + 1]
    ok = stream.verify_chunk_invariance(ref, tgt, cfg, sizes)
    check("frames identical for chunk sizes %s" % (sizes,), ok)


# ---------------------------------------------------------------------------
# 7. order invariance and determinism of the full run
# ---------------------------------------------------------------------------
def _build_demo(dirpath, n_sec=5.0, seed=3):
    n = int(n_sec * FS)
    specs = [dict(name="t1", delay_ms=12.5, snr_db=20.0),
             dict(name="t2", delay_ms=-6.25, snr_db=25.0, length_ratio=0.8),
             dict(name="t3", delay_ms=3.75, snr_db=15.0, drift_ppm=250.0),
             dict(name="t4", two_segment=(5.0, 13.0, 2.4), snr_db=20.0)]
    ref, targets = synth.multi_track(n, FS, specs, seed=seed, rt60=0.35,
                                     ref_drr_db=0.0, tgt_drr_db=-3.0)
    dsp.write_wav16(os.path.join(dirpath, "ref.wav"), ref, FS)
    names = [("ref", "ref.wav")]
    for t in targets:
        fn = "%s.wav" % t["name"]
        dsp.write_wav16(os.path.join(dirpath, fn), t["signal"], FS)
        names.append((t["name"], fn))
    return names, specs


def test_order_and_determinism():
    print("\n7. Track-order invariance and determinism")
    tmp = tempfile.mkdtemp(prefix="align-test-")
    try:
        names, specs = _build_demo(tmp)
        base_digest = None
        base_payload = None
        # the reference must be first, so permutations keep the reference
        # fixed and only reorder the targets -- which is exactly the
        # requirement ("randomly reorder the tracks, summary must agree").
        orders = [[1, 2, 3, 4], [4, 2, 1, 3], [3, 1, 4, 2], [2, 4, 3, 1]]
        for perm in orders:
            tracks = [dict(name=names[0][0], pcm=names[0][1])]
            tracks += [dict(name=names[i][0], pcm=names[i][1]) for i in perm]
            req = dict(tracks=tracks, fs=FS, max_lag_ms=20,
                       _base=os.path.abspath(tmp))
            out = os.path.join(tmp, "out_%s.json" % "_".join(map(str, perm)))
            payload = align.run(req, out)
            if base_digest is None:
                base_digest = payload["summary_digest"]
                base_payload = payload
            else:
                check("order %s: digest identical" % (perm,),
                      payload["summary_digest"] == base_digest,
                      "%s vs %s" % (payload["summary_digest"][:8],
                                    base_digest[:8]))

        # determinism: repeat the same request, byte-identical output
        out_a = os.path.join(tmp, "det_a.json")
        out_b = os.path.join(tmp, "det_b.json")
        tracks = [dict(name=a, pcm=b) for a, b in names]
        req = dict(tracks=tracks, fs=FS, max_lag_ms=20,
                   _base=os.path.abspath(tmp))
        align.run(req, out_a)
        align.run(req, out_b)
        same = open(out_a, "rb").read() == open(out_b, "rb").read()
        check("repeat run byte-identical", same)

        # truth check for the demo material
        for t in base_payload["tracks"]:
            spec = next(s for s in specs if s["name"] == t["name"])
            if "two_segment" in spec:
                truth = spec["two_segment"][0]
                ok_segs = len(t["segments"]) >= 2
                check("%s: two segments detected" % t["name"], ok_segs,
                      "segments %s" %
                      [(s["kind"], round(s.get("lag_ms", 0), 2)) for s in t["segments"]])
            elif spec.get("drift_ppm"):
                truth = spec["delay_ms"]
                near = abs((t["total_lag_ms"] or 0) - truth)
                check("%s: drift track near %.2f ms" % (t["name"], truth),
                      near <= 0.5, "est %.3f ms, drift %.1f ppm" % (
                          t["total_lag_ms"] or 0, t["drift_ppm"]))
            else:
                truth = spec["delay_ms"]
                near = abs((t["total_lag_ms"] or 0) - truth)
                check("%s: |err| <= 0.5 ms (truth %.2f)" % (t["name"], truth),
                      near <= 0.5, "est %.3f ms" % (t["total_lag_ms"] or 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. output contract
# ---------------------------------------------------------------------------
def test_output_contract():
    print("\n8. Output contract")
    tmp = tempfile.mkdtemp(prefix="align-out-")
    try:
        names, _specs = _build_demo(tmp, n_sec=5.0)
        tracks = [dict(name=a, pcm=b) for a, b in names]
        req = dict(tracks=tracks, fs=FS, max_lag_ms=20,
                   _base=os.path.abspath(tmp))
        req_path = os.path.join(tmp, "req.json")
        with open(req_path, "w") as f:
            json.dump(dict(tracks=tracks, fs=FS, max_lag_ms=20), f)
        out_path = os.path.join(tmp, "output.json")
        # run through the real CLI entry point
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        rc = subprocess.call([sys.executable, os.path.join(here, "align.py"),
                              req_path, out_path])
        check("CLI exit code 0", rc == 0, "rc=%d" % rc)
        payload = json.load(open(out_path))
        required = ["tracks", "mix", "notch_db_improve", "summary_digest"]
        check("required top-level keys present",
              all(k in payload for k in required), str(sorted(payload)))
        check("mix file written",
              os.path.exists(os.path.join(tmp, payload["mix"])))
        per_track = ["name", "lag_ms", "frac_ms", "reliable", "conf", "curve",
                     "segments", "unaligned", "compensation", "multi_segment",
                     "coverage_ms", "total_ms"]
        t = payload["tracks"][0]
        missing = [k for k in per_track if k not in t]
        check("per-track fields present", not missing, str(missing))
        check("tracks sorted by name",
              [x["name"] for x in payload["tracks"]] ==
              sorted(x["name"] for x in payload["tracks"]))
        # lag_ms must be an integer number of samples' worth of ms
        lag_samples = t["lag_ms"] * FS / 1000.0
        check("lag_ms is integer samples",
              abs(lag_samples - round(lag_samples)) < 1e-6,
              "lag_ms %.6f -> %.6f samples" % (t["lag_ms"], lag_samples))
        check("frac_ms is sub-sample",
              abs(t["frac_ms"]) < 1000.0 / FS,
              "frac_ms %.6f" % t["frac_ms"])
        check("notch_db_improve >= 12 dB in demo",
              payload["notch_db_improve"] >= 12.0,
              "%.1f dB" % payload["notch_db_improve"])
        check("curve has time and lag fields",
              all("t_ms" in p and "lag_ms" in p for p in t["curve"]),
              "%d points" % len(t["curve"]))
        check("compensation map covers the track",
              len(t["compensation"]) > 0,
              "%d entries" % len(t["compensation"]))
        # reason codes must be from the documented set
        allowed = {"insufficient_overlap", "reference_silent", "track_silent",
                   "low_detectability", "beyond_max_comp", "unaligned"}
        bad = [u["reason"] for tt in payload["tracks"]
               for u in tt["unaligned"] if u["reason"] not in allowed]
        check("unaligned reasons are from the documented set", not bad, str(bad))
        # a two-delay track must flag multi_segment and report both pieces
        t4 = next((tt for tt in payload["tracks"] if tt["multi_segment"]), None)
        check("multi-delay track flagged and segmented",
              t4 is not None and len(t4["segments"]) >= 2,
              str([(s["kind"], s.get("lag_ms")) for s in (t4["segments"] if t4 else [])]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mixed_rates():
    print("\n9. Mixed sample rates")
    tmp = tempfile.mkdtemp(prefix="align-rate-")
    try:
        n48 = int(4.0 * 48000)
        ref, targets = synth.multi_track(
            n48, 48000, [dict(name="t", delay_ms=9.5, snr_db=40.0)], seed=3)
        dsp.write_wav16(os.path.join(tmp, "ref48.wav"), ref, 48000)
        tgt16 = dsp.resample_fft(targets[0]["signal"], 16000 / 48000.0)
        dsp.write_wav16(os.path.join(tmp, "t16.wav"), tgt16, 16000)
        req = dict(tracks=[dict(name="ref", pcm="ref48.wav"),
                           dict(name="t", pcm="t16.wav")],
                   fs=FS, max_lag_ms=20, _base=os.path.abspath(tmp))
        payload = align.run(req, os.path.join(tmp, "o.json"))
        t = payload["tracks"][0]
        check("48k ref + 16k target: |err| <= 0.5 ms",
              abs((t["total_lag_ms"] or 0) - 9.5) <= 0.5,
              "est %.4f ms" % (t["total_lag_ms"] or 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("Multitrack alignment acceptance tests")
    test_accuracy()
    test_interval_coverage()
    test_low_freq_dominance()
    test_comb()
    test_segments()
    test_unaligned()
    test_chunk_invariance()
    test_order_and_determinism()
    test_output_contract()
    test_mixed_rates()
    print("\n%d failure(s)" % len(FAIL))
    if FAIL:
        for f in FAIL:
            print("  - %s" % f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

