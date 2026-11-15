#!/usr/bin/env python3
"""Multi-track phase alignment: ``python3 align.py req.json output.json``.

Request format (see README)::

    {
      "tracks": [{"name": "ref", "pcm": "ref.wav"},
                 {"name": "t1",  "pcm": "t1.wav"}],
      "fs": 16000,
      "max_lag_ms": 20,
      "options": {}            # optional overrides of analysis.DEFAULTS
    }

The first track is the timing reference.  Every other track is estimated
against it and compensated onto it.

Output::

    {
      "tracks": [ {name, lag_ms, frac_ms, reliable, conf, curve, segments,
                   unaligned, compensation} ],
      "mix": "mix.wav",
      "notch_db_improve": 44.7,
      "summary_digest": "..."
    }

``lag_ms`` is the integer-sample part of the offset and ``frac_ms`` the
residual sub-sample part (their sum is the full estimate), so ``lag_ms``
alone is always expressible as an integer shift.

Determinism: the ``tracks`` array is sorted by name and every summary value
derives from delays only, never from track order, so any permutation of the
request yields the same ``summary_digest``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import analysis
import compensate
import dsp


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def _parse(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: align.py req.json output.json\n")
        raise SystemExit(2)
    return argv[1], argv[2]


def resolve_paths(req, base=None):
    """Attach an absolute ``_path`` to every track (idempotent).

    Kept separate from :func:`_read_request` so the pipeline can also be
    driven from an in-memory request (tests, library use).
    """
    if base is None:
        base = req.get("_base") or os.getcwd()
    for t in req["tracks"]:
        p = t["pcm"]
        t["_path"] = p if os.path.isabs(p) else os.path.join(base, p)
    return req


def _read_request(path):
    with open(path, "r") as f:
        req = json.load(f)
    req["_base"] = os.path.dirname(os.path.abspath(path))
    resolve_paths(req)
    if not req.get("fs"):
        _, fs = dsp.read_wav(req["tracks"][0]["_path"])
        req["fs"] = fs
    return req


def _resample_if_needed(x, fs_in, fs_out):
    if fs_in == fs_out:
        return x
    return dsp.resample_fft(x, fs_out / float(fs_in))


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def _curve_points(res, max_points=400):
    values = res["values"]
    centers = res["plan"]["center_ms"]
    n = len(values)
    step = max(1, n // max_points)
    pts = []
    for k in range(0, n, step):
        v = values[k]
        if v is None:
            pts.append({"t_ms": round(centers[k], 3), "lag_ms": None, "lh": 0.0})
        else:
            pts.append({"t_ms": round(centers[k], 3),
                        "lag_ms": round(v["lag_ms"], 4),
                        "lh": round(dsp.clamp(v["rho"], 0.0, 1.0), 4)})
    return pts


def _track_summary(name, res, cfg):
    pieces = res["pieces"]
    total = res["total_ms"]
    aligned_ms = 0.0
    unaligned = []
    confs = []
    for p in pieces:
        span = p["t1_ms"] - p["t0_ms"]
        if p["kind"] == "gap":
            if span > 0.0:
                unaligned.append({
                    "t0_ms": round(p["t0_ms"], 3),
                    "t1_ms": round(p["t1_ms"], 3),
                    "reason": p.get("reason") or "unaligned",
                })
            continue
        aligned_ms += span
        confs.append(p["conf"])
    covered_end = 0.0
    for p in pieces:
        if p["kind"] != "gap":
            covered_end = max(covered_end, p["t1_ms"])
    # A short track simply has no data at the end; report that tail once, and
    # do not duplicate a tail that a gap piece already covers.
    if total - covered_end > 1e-6:
        last_gap_t1 = max((u["t1_ms"] for u in unaligned), default=-1.0)
        if covered_end > last_gap_t1 + 1e-6:
            unaligned.append({
                "t0_ms": round(covered_end, 3),
                "t1_ms": round(total, 3),
                "reason": "insufficient_overlap",
            })
    conf = dsp.median(confs) if confs else 0.0
    const_pieces = [p for p in pieces if p["kind"] == "const"]
    main = None
    if const_pieces:
        longest = max(const_pieces, key=lambda p: p["t1_ms"] - p["t0_ms"])
        main = longest["params"][0]
    else:
        for p in pieces:
            if p["kind"] == "line":
                main = p["params"][0] + p["params"][1] * p["t0_ms"]
                break
    # ``lag_ms``/``frac_ms`` describe the dominant (longest) constant stretch;
    # a track with several distinct delays reports them all in ``segments``
    # and signals the ambiguity through ``multi_segment``.
    multi_segment = len([p for p in pieces if p["kind"] != "gap"]) > 1
    segments_out = []
    for p in pieces:
        if p["kind"] == "gap":
            continue
        seg = {"kind": p["kind"],
               "t0_ms": round(p["t0_ms"], 3),
               "t1_ms": round(p["t1_ms"], 3)}
        if p["kind"] == "const":
            seg["lag_ms"] = round(p["params"][0], 4)
        else:
            seg["lag_t0_ms"] = round(p["params"][0] + p["params"][1] * p["t0_ms"], 4)
            seg["drift_ppm"] = round(p["params"][1] * 1e6, 2)
        seg["conf"] = round(p["conf"], 4)
        segments_out.append(seg)
    drift_ppm = next((s["drift_ppm"] for s in segments_out if "drift_ppm" in s), 0.0)
    if main is None:
        lag_ms, frac_ms = 0.0, 0.0
        reliable = False
    else:
        lag_samp = main * cfg["fs"] / 1000.0
        int_samp = int(math.floor(lag_samp))
        lag_ms = int_samp / float(cfg["fs"]) * 1000.0
        frac_ms = (lag_samp - int_samp) / float(cfg["fs"]) * 1000.0
        reliable = (conf >= cfg["min_conf"]) and aligned_ms >= total * 0.5
    return {
        "name": name,
        "lag_ms": round(lag_ms, 4),
        "frac_ms": round(frac_ms, 4),
        "total_lag_ms": round(main, 4) if main is not None else None,
        "reliable": bool(reliable),
        "conf": round(conf, 4),
        "drift_ppm": round(drift_ppm, 2),
        "multi_segment": bool(multi_segment),
        "coverage_ms": round(aligned_ms, 1),
        "total_ms": round(total, 1),
        "curve": _curve_points(res),
        "segments": segments_out,
        "unaligned": unaligned,
    }


def summary_digest(tracks):
    """Track-order independent digest of the alignment summary.

    Only delays, confidence and coverage are hashed, keyed by name and sorted,
    so any permutation of the request produces the same digest.  Floats are
    rounded first, so no ordering artefact can perturb the last bits.
    """
    h = hashlib.sha256()
    for t in sorted(tracks, key=lambda t: t["name"]):
        h.update(("%s|%.4f|%.4f|%d|%.4f|%.1f|%.1f\n" % (
            t["name"], t["lag_ms"], t["frac_ms"], 1 if t["reliable"] else 0,
            t["conf"], t["coverage_ms"], t["total_ms"])).encode("utf-8"))
        for s in t["segments"]:
            h.update(("%s|%.3f|%.3f|%.4f|%.4f\n" % (
                s["kind"], s["t0_ms"], s["t1_ms"],
                s.get("lag_ms", s.get("lag_t0_ms", 0.0)),
                s["conf"])).encode("utf-8"))
        for u in t["unaligned"]:
            h.update(("gap|%.3f|%.3f|%s\n" % (
                u["t0_ms"], u["t1_ms"], u["reason"])).encode("utf-8"))
    return h.hexdigest()


def compensation_map(pieces):
    """The integer + fractional mapping the mixer applied, piece by piece."""
    out = []
    for p in pieces:
        if p["kind"] == "gap":
            out.append({"t0_ms": round(p["t0_ms"], 3),
                        "t1_ms": round(p["t1_ms"], 3),
                        "aligned": False,
                        "reason": p.get("reason") or "unaligned"})
            continue
        entry = {"t0_ms": round(p["t0_ms"], 3),
                 "t1_ms": round(p["t1_ms"], 3),
                 "aligned": True}
        if p["kind"] == "const":
            entry["lag_ms"] = round(p["params"][0], 4)
        else:
            entry["lag_t0_ms"] = round(p["params"][0] + p["params"][1] * p["t0_ms"], 4)
            entry["lag_t1_ms"] = round(p["params"][0] + p["params"][1] * p["t1_ms"], 4)
        out.append(entry)
    return out




# ---------------------------------------------------------------------------
# main pipeline
# ---------------------------------------------------------------------------
def run(req, out_path):
    resolve_paths(req)
    cfg = analysis.resolve_config(req)
    fs = cfg["fs"]
    tracks = req["tracks"]
    ref_name = tracks[0]["name"]
    ref, ref_fs = dsp.read_wav(tracks[0]["_path"])
    ref = _resample_if_needed(ref, ref_fs, fs)

    results = []
    aligned = [(ref_name, ref)]
    for t in tracks[1:]:
        x, xfs = dsp.read_wav(t["_path"])
        x = _resample_if_needed(x, xfs, fs)
        res = analysis.analyze_pair(ref, x, cfg)
        summ = _track_summary(t["name"], res, cfg)
        out_len = compensate.compensated_length(len(x), res["pieces"], fs,
                                                cfg["max_comp_ms"])
        y, applied = compensate.compensate_track(x, res["pieces"], fs, cfg,
                                                 out_len=out_len)
        summ["compensation"] = compensation_map(res["pieces"])
        summ["applied_blocks"] = len(applied)
        results.append(summ)
        aligned.append((t["name"], y))
        aligned_len = max(len(v) for _, v in aligned)
        keep = []
        for nm, v in aligned:
            keep.append((nm, v + [0.0] * (aligned_len - len(v))))
        aligned = keep

    # make every aligned track the same length (already done above)
    L = max(len(v) for _, v in aligned) if aligned else 0
    aligned = [(nm, v[:L] if len(v) >= L else v + [0.0] * (L - len(v)))
               for nm, v in aligned]

    mix = compensate.mix_tracks([v for _, v in aligned], L)
    mix_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "mix.wav")
    dsp.write_wav16(mix_path, mix, fs)

    # Comb depth is measured on the *same* pair of signals before and after
    # compensation, so the number isolates the alignment effect: the notch
    # depth of summing the tracks as captured minus that of summing them after
    # alignment.  A perfectly aligned sum has no notches at all (0 dB), while
    # a misaligned pair gives |cos(pi f d)| notches.
    raw = []
    raw_len = 0
    for t in tracks:
        x, xfs = dsp.read_wav(t["_path"])
        x = _resample_if_needed(x, xfs, fs)
        raw.append(x)
        raw_len = max(raw_len, len(x))
    raw = [x + [0.0] * (raw_len - len(x)) for x in raw]
    if len(raw) > 1:
        seq_len = min(len(v) for _, v in aligned)
        keep = min(seq_len, raw_len)
        raw_pairs = [x[:keep] for x in raw]
        ali_pairs = [v[:keep] for _, v in aligned]
        d_raw, _ = compensate.comb_notch_db(raw_pairs, fs)
        d_ali, _ = compensate.comb_notch_db(ali_pairs, fs)
    else:
        d_raw = d_ali = 0.0
    improve = d_raw - d_ali

    payload = {
        "tracks": sorted(results, key=lambda t: t["name"]),
        "reference": ref_name,
        "mix": os.path.basename(mix_path),
        "notch_db_improve": round(improve, 2),
        "notch_db_unaligned": round(d_raw, 2),
        "notch_db_aligned": round(d_ali, 2),
        "fs": fs,
    }
    payload["summary_digest"] = summary_digest(payload["tracks"])
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return payload


def main(argv):
    req_path, out_path = _parse(argv)
    req = _read_request(req_path)
    run(req, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
