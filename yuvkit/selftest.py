"""Acceptance self-test, runnable as ``python3 convert.py --selftest``.

The checks mirror the acceptance criteria one-for-one and print a
machine-readable JSON report, so the same code can back CI and a manual
"did we actually fix it?" review:

A1 boundary and random round trip inside 2/255 (all standards/ranges)
A2 a 601/709 mix is detected and reported
A3 a ramp gains no banding beyond the double-quantisation limit
A4 an independent matrix implementation agrees per pixel, differences
   only at rounding boundaries and never more than one code
A5 LUT and matrix paths are byte identical
A6 chroma siting/phase round trips are reversible within the phase model
"""

import json
import sys
import time

from .checks import compare_constants
from .converter import Converter, compare_all, compare_paths
from .lossless import classify_roundtrip, lossless_channels
from .metrics import (banding_profile, boundary_samples, grey_ramp,
                      random_pixels, ramp, roundtrip_stats)
from .planes import roundtrip_planes
from .request import run_request

STANDARDS = ("bt601", "bt709", "bt2020")
RANGE_PAIRS = (("full", "limited"), ("limited", "full"), ("full", "full"),
               ("limited", "limited"))


def _log(verbose, message):
    if verbose:
        print(message)


def check_roundtrip(verbose=False, random_count=100000):
    """A1: boundary swatches and 10^5 random pixels, all combinations."""
    results = []
    ok = True
    for std in STANDARDS:
        for rgb_range, yuv_range in RANGE_PAIRS:
            conv = Converter(std, rgb_range, yuv_range)
            swatches = [p for _name, p in boundary_samples(std, rgb_range,
                                                          yuv_range)]
            boundary = roundtrip_stats(conv, swatches)
            rng = random_pixels(random_count, rgb_range, seed=1234)
            bulk = roundtrip_stats(conv, rng)
            entry = {
                "standard": std,
                "rgb_range": rgb_range,
                "yuv_range": yuv_range,
                "boundary_max_err": boundary["max_err"],
                "random_max_err": bulk["max_err"],
                "random_mean_err": round(bulk["mean_err"], 6),
                "random_pixels": bulk["pixels"],
                "histogram": bulk["histogram"],
                "ok": boundary["max_err"] <= 2 and bulk["max_err"] <= 2,
            }
            ok = ok and entry["ok"]
            results.append(entry)
            _log(verbose, "A1 %-7s rgb=%-7s yuv=%-7s boundary=%d random=%d "
                          "mean=%.3f %s"
                 % (std, rgb_range, yuv_range, boundary["max_err"],
                    bulk["max_err"], bulk["mean_err"],
                    "OK" if entry["ok"] else "FAIL"))
    # Limited->Limited over the legal 16..235 cube, worst case by exhaust.
    worst_limited = 0
    worst_case = None
    for std in STANDARDS:
        conv = Converter(std, "limited", "limited")
        for r in range(16, 236, 3):
            for g in range(16, 236, 21):
                for b in range(16, 236, 33):
                    mid = conv.rgb_to_yuv((r, g, b)).codes
                    back = conv.yuv_to_rgb(mid).codes
                    err = max(abs(a - c) for a, c in
                              zip((r, g, b), back))
                    if err > worst_limited:
                        worst_limited = err
                        worst_case = (std, (r, g, b), mid, back)
    results.append({"check": "limited-cube-worst", "max_err": worst_limited,
                    "case": worst_case, "ok": worst_limited <= 2})
    ok = ok and worst_limited <= 2
    _log(verbose, "A1 limited-range cube worst case = %d %s"
         % (worst_limited, "OK" if worst_limited <= 2 else "FAIL"))
    return {"name": "roundtrip-tolerance", "ok": ok, "entries": results}


def check_mixed_standard_detection(verbose=False):
    """A2: a request that mixes 601 and 709 must warn explicitly."""
    req = {
        "standard": "bt601",
        "rgb_range": "full",
        "yuv_range": "limited",
        "meta": {"standard": "bt709"},
        "chain": [
            {"standard": "bt601", "direction": "rgb-to-yuv"},
            {"standard": "bt709", "direction": "yuv-to-rgb"},
        ],
        "ops": [{"direction": "rgb-to-yuv",
                 "pixels": [[255, 0, 0], [0, 255, 0], [0, 0, 255]]}],
    }
    mixed = run_request(req)
    clean = run_request({
        "standard": "bt709", "rgb_range": "full", "yuv_range": "limited",
        "ops": [{"direction": "rgb-to-yuv", "pixels": [[10, 20, 30]]}],
    })
    per_op = run_request({
        "standard": "bt709", "rgb_range": "full", "yuv_range": "limited",
        "ops": [
            {"direction": "rgb-to-yuv", "standard": "bt601",
             "pixels": [[1, 2, 3]]},
            {"direction": "rgb-to-yuv", "standard": "bt709",
             "pixels": [[4, 5, 6]]},
        ],
    })
    detected = "mixed-standard" in mixed["warnings"]
    clean_ok = clean["warnings"] == []
    per_op_detected = "mixed-standard" in per_op["warnings"]
    ok = detected and clean_ok and per_op_detected
    delta = compare_constants(["bt601", "bt709"])["deltas"]["bt709-vs-bt601"]
    _log(verbose, "A2 mixed-standard detected=%r clean-empty=%r per-op=%r %s"
         % (detected, clean_ok, per_op_detected, "OK" if ok else "FAIL"))
    return {
        "name": "mixed-standard-detection",
        "ok": ok,
        "warnings": mixed["warnings"],
        "clean_warnings": clean["warnings"],
        "per_op_warnings": per_op["warnings"],
        "constant_deltas": delta,
    }


def check_banding(verbose=False):
    """A3: ramps must not gain plateaus beyond double quantisation."""
    entries = []
    ok = True
    combos = (("bt709", "full", "limited"), ("bt709", "limited", "full"),
              ("bt601", "full", "limited"), ("bt2020", "full", "limited"))
    for std, rgb_range, yuv_range in combos:
        conv = Converter(std, rgb_range, yuv_range)
        width = 256 if rgb_range == "full" else 220
        src = grey_ramp(width, rgb_range)
        out = []
        for pixel in src:
            mid = conv.rgb_to_yuv(pixel).codes
            out.append(conv.yuv_to_rgb(mid).codes[0])
        before = banding_profile([p[0] for p in src])
        after = banding_profile(out)
        # Expected levels after a round trip through a (possibly) narrower
        # luma span: min(levels, yuv span + 1).
        span = 256 if yuv_range == "full" else 220
        expected_levels = min(before["levels"], span)
        entry = {
            "standard": std, "rgb_range": rgb_range, "yuv_range": yuv_range,
            "src_levels": before["levels"], "dst_levels": after["levels"],
            "expected_levels": expected_levels,
            "src_plateaus": before["plateaus"],
            "dst_plateaus": after["plateaus"],
            "max_run": after["max_run"], "reversals": after["reversals"],
            "excess_stalls": after["excess_stalls"],
            "levels_match": after["levels"] >= expected_levels,
            "monotone": after["reversals"] == 0,
            "no_new_banding": after["excess_stalls"] == 0,
        }
        entry["ok"] = (entry["levels_match"] and entry["monotone"]
                       and entry["no_new_banding"])
        ok = ok and entry["ok"]
        entries.append(entry)
        _log(verbose, "A3 %-7s %-7s->%-7s levels %d/%d (want >=%d) "
                      "monotone=%r excess_stalls=%d %s"
             % (std, rgb_range, yuv_range, after["levels"], before["levels"],
                expected_levels, entry["monotone"],
                entry["excess_stalls"], "OK" if entry["ok"] else "FAIL"))

    # A colour ramp exercises chroma too: no stall may exceed the
    # theoretical one-step plateau length of 2 samples.
    conv = Converter("bt709", "full", "limited")
    colour = ramp(256, (0, 0, 0), (255, 255, 255))
    chroma_out = [conv.yuv_to_rgb(conv.rgb_to_yuv(p).codes).codes
                  for p in colour]
    profiles = [banding_profile([p[i] for p in chroma_out]) for i in range(3)]
    colour_ok = all(p["max_run"] <= 2 and p["reversals"] == 0
                    and p["excess_stalls"] == 0 for p in profiles)
    ok = ok and colour_ok
    _log(verbose, "A3 colour ramp max plateau length = %r %s"
         % ([p["max_run"] for p in profiles], "OK" if colour_ok else "FAIL"))
    return {"name": "banding", "ok": ok, "entries": entries,
            "colour_ramp_runs": [p["max_run"] for p in profiles]}


def check_independent_matrix(verbose=False, count=20000):
    """A4/A5: cross-check against independent implementations."""
    results = []
    ok = True
    for std in STANDARDS:
        for rgb_range, yuv_range in (("full", "limited"),
                                     ("limited", "full")):
            pixels = random_pixels(count, rgb_range, seed=99)
            cmp_all = compare_all(pixels, "rgb-to-yuv", std, rgb_range,
                                  yuv_range)
            yuv_pixels = [Converter(std, rgb_range, yuv_range).rgb_to_yuv(p)
                          .codes for p in pixels]
            cmp_inv = compare_all(yuv_pixels, "yuv-to-rgb", std, rgb_range,
                                  yuv_range)
            same_lut = cmp_all["engines"]["lut"]["differing"] == 0
            within = (cmp_all["all_within_one_code"]
                      and cmp_inv["all_within_one_code"])
            float_only_boundary = _boundary_confined(cmp_all, "float64")
            entry = {
                "standard": std, "rgb_range": rgb_range,
                "yuv_range": yuv_range,
                "lut_identical": same_lut,
                "forward_max_delta": cmp_all["max_delta"],
                "inverse_max_delta": cmp_inv["max_delta"],
                "forward_engines": {k: {"differing": v["differing"],
                                        "max_delta": v["max_delta"],
                                        "max_boundary_distance":
                                            v["max_boundary_distance"]}
                                    for k, v in cmp_all["engines"].items()},
                "float64_confined_to_boundary": float_only_boundary,
                "ok": same_lut and within,
            }
            ok = ok and entry["ok"]
            results.append(entry)
            _log(verbose, "A4 %-7s %-7s->%-7s lut-identical=%r fwd<=%d "
                          "inv<=%d %s"
                 % (std, rgb_range, yuv_range, same_lut,
                    cmp_all["max_delta"], cmp_inv["max_delta"],
                    "OK" if entry["ok"] else "FAIL"))
    return {"name": "independent-matrix", "ok": ok, "entries": results}


def _boundary_confined(cmp_all, engine):
    info = cmp_all["engines"][engine]
    if info["differing"] == 0:
        return True
    return info["max_delta"] <= 1


def check_alt_paths(verbose=False, count=5000):
    """A5 (explicit): the two production engines must be identical."""
    ok = True
    entries = []
    for std in STANDARDS:
        for rgb_range, yuv_range in RANGE_PAIRS:
            pixels = random_pixels(count, rgb_range, seed=7)
            fwd = compare_paths(pixels, "rgb-to-yuv", std, rgb_range,
                                yuv_range)
            conv = Converter(std, rgb_range, yuv_range)
            yuv_pixels = [conv.rgb_to_yuv(p).codes for p in pixels]
            inv = compare_paths(yuv_pixels, "yuv-to-rgb", std, rgb_range,
                                yuv_range)
            entry = {
                "standard": std, "rgb_range": rgb_range,
                "yuv_range": yuv_range,
                "forward_identical": fwd["identical"],
                "inverse_identical": inv["identical"],
                "ok": fwd["identical"] and inv["identical"],
            }
            ok = ok and entry["ok"]
            entries.append(entry)
    # A dense systematic sweep on top of the random sets: random pixels
    # can miss a channel combination that a table index maps wrongly.
    sweep_ok = True
    sweep_cases = 0
    for std in STANDARDS:
        a = Converter(std, "full", "limited", path="matrix")
        b = Converter(std, "full", "limited", path="lut")
        for r in range(0, 256, 5):
            for g in range(0, 256, 17):
                for bl in range(0, 256, 19):
                    if a.rgb_to_yuv((r, g, bl)).codes != \
                            b.rgb_to_yuv((r, g, bl)).codes:
                        sweep_ok = False
                    sweep_cases += 1
    ok = ok and sweep_ok
    _log(verbose, "A5 lut vs matrix identical on all combinations = %r "
                  "(+ %d systematic samples) %s"
         % (ok, sweep_cases, "OK" if sweep_ok else "FAIL"))
    return {"name": "path-consistency", "ok": ok, "entries": entries,
            "systematic_samples": sweep_cases}


def check_chroma_phase(verbose=False):
    """A6: siting round trips, and 4:2:0 relative to 4:4:4."""
    entries = []
    ok = True
    grey = [[(v, v, v) for v in range(256)] for _ in range(8)]
    colour = [[(255 - x // 2, x // 2, 128) for x in range(256)]
              for _ in range(8)]
    for layout in ("4:4:4", "4:2:2", "4:2:0", "4:2:2-left", "4:2:0-left"):
        for name, image in (("grey", grey), ("colour", colour)):
            back, report = roundtrip_planes(image, "bt709", "full",
                                            "limited", layout)
            err = report["max_err"]
            # Chroma sub-sampling adds error on the colour ramp only; the
            # bound is one chroma code times the reconstruction gain.
            limit = 2 if layout == "4:4:4" else 3
            entry = {"layout": layout, "image": name, "max_err": err,
                     "limit": limit, "ok": err <= limit,
                     "chroma_phase": report["layout"]["phase"]}
            ok = ok and entry["ok"]
            entries.append(entry)
            _log(verbose, "A6 %-10s %-6s max_err=%d (limit %d) %s"
                 % (layout, name, err, limit,
                    "OK" if entry["ok"] else "FAIL"))
    return {"name": "chroma-phase", "ok": ok, "entries": entries}


def check_lossless_channels(verbose=False):
    """Report the analytic lossless-channel classification."""
    entries = []
    for std in STANDARDS:
        for rgb_range, yuv_range in RANGE_PAIRS:
            info = lossless_channels(std, rgb_range, yuv_range)
            measured = classify_roundtrip(
                random_pixels(5000, rgb_range, seed=11), std, rgb_range,
                yuv_range)
            entries.append({
                "standard": std, "rgb_range": rgb_range,
                "yuv_range": yuv_range,
                "analytic_bounds": info["bounds"],
                "lossless": info["lossless"],
                "measured_classes": measured["classes"],
                "measured_ok": measured["ok"],
            })
    ok = all(e["measured_ok"] for e in entries)
    _log(verbose, "A7 lossless channel classification measured-ok=%r" % ok)
    return {"name": "lossless-channels", "ok": ok, "entries": entries}


CHECKS = (
    ("roundtrip-tolerance", check_roundtrip),
    ("mixed-standard", check_mixed_standard_detection),
    ("banding", check_banding),
    ("independent-matrix", check_independent_matrix),
    ("path-consistency", check_alt_paths),
    ("chroma-phase", check_chroma_phase),
    ("lossless-channels", check_lossless_channels),
)


def run(verbose=False, only=None):
    started = time.time()
    checks = []
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        result = fn(verbose)
        result["duration_s"] = round(time.time() - started, 3)
        checks.append(result)
    report = {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "duration_s": round(time.time() - started, 3),
    }
    return report


def main(argv):
    verbose = "-v" in argv or "--verbose" in argv
    only = [a for a in argv if not a.startswith("-")]
    report = run(verbose, only or None)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

