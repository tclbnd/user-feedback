"""End to end pipeline: load -> meter -> decompose -> local tone map -> report.

Output contract
---------------
    ev        float            exposure compensation chosen by the meter
    out       [[float]]        tone mapped display values in [0, 1]
    zone_ev   [[float]]        per zone vote (EV relative to the key)
    digest    str              sha256 over the quantised output
    stats     dict             metering statistics (integer accumulators)
    zones     [dict]           per zone contribution breakdown
    curve     dict             tone curve parameters (tiling invariant)
    invariant str              digest over curve params + output digest
    rgb       [[[r,g,b]]]      colour reconstruction, only for colour input

INVARIANCE
----------
`curve` and `digest` are functions of the global statistics only, and those
globals are integer sums over the zones (metering.py).  Re-running with a
different `tile` / `zones` / `zone_shuffle` therefore reproduces `ev`,
`curve` and `digest` bit for bit.
"""
import hashlib
import json
import math
import os

from . import tone
from .config import parse
from .guided import bilateral_base
from .imageio import (check_partition, load, safe_luma, shuffle_order,
                      split_luma_chroma, zone_grid)
from .metering import meter


# ------------------------------------------------------------------ curves --
def curve_params(stats, cfg):
    """Tone curve parameters (a function of global statistics only)."""
    return {
        "ev": stats["metering_ev"],
        "ev_bias": stats["ev_bias"],
        "key": stats["key"],
        "log_key": stats["log_key"],
        "mean_log_ev": stats["mean_log_ev"],
        "white_point": stats["white_point"],
        "shadow_gain_cap_ev": stats["lift_cap_ev"],
        "shadow_gain_cap_linear": cfg["max_dark_gain"],
        "lift_k": cfg["lift_k"],
        "pivot": cfg["pivot"],
        "detail_gain": cfg["detail_gain"],
        "gamma": cfg["gamma"],
        "curve_coeffs": {"A": tone.A, "B": tone.B, "C": tone.C},
    }


def curve_key(curve):
    """Canonical text of the curve parameters (stable across runs)."""
    return json.dumps(curve, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


# ------------------------------------------------------------------ digest --
def output_digest(values, qbits=20):
    """sha256 over the output quantised on a 2**qbits grid.

    Quantising keeps the digest meaningful: it proves two runs agree to about
    1e-6 of a display unit, which is far below any visible difference, while
    staying immune to last-bit libm noise on another platform.
    """
    q = int(round((1 << qbits) * 1.0))
    items = [int(round(v * q)) for v in values]
    payload = ",".join("%d" % v for v in items).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _stats_for_tone(cfg, stats):
    return {
        "metering_ev": stats["metering_ev"],
        "log_key": stats["log_key"],
        "lift_cap_ev": stats["lift_cap_ev"],
    }


# -------------------------------------------------------------------- run ---
def run(req, base_dir="."):
    cfg, href = parse(req)
    y, rgbinfo, h, w = load(href, base_dir)
    ysafe = safe_luma(y, h, w)

    # ---- zone geometry and visit order ----
    bounds_r, bounds_c = zone_grid(h, w, cfg["tile"], cfg["zones"])
    check_partition(bounds_r, bounds_c, h, w)
    n_zones = (len(bounds_r) - 1) * (len(bounds_c) - 1)
    order = shuffle_order(n_zones, cfg["zone_shuffle"])

    # ---- metering: integer sums, independent of the partition ----
    stats, zones, _hist = meter(ysafe, h, w, bounds_r, bounds_c, order, cfg)
    stats["lift_cap_ev"] = math.log2(cfg["max_dark_gain"])

    # ---- edge aware decomposition of the log luminance ----
    log_y = [0.0] * (h * w)
    for r in range(h):
        base = r * w
        row = ysafe[r]
        for c in range(w):
            log_y[base + c] = math.log2(row[c])
    B, D = bilateral_base(log_y, h, w, cfg)

    # ---- local tone mapping with the capped shadow lift on the base ----
    tstats = _stats_for_tone(cfg, stats)
    display = tone.render(B, D, h, w, cfg, tstats)

    # ---- brightness / colour recombination ----
    rgb_out = _recombine(display, y, rgbinfo, h, w, cfg)

    out_rows = [[display[r * w + c] for c in range(w)] for r in range(h)]
    nzc = len(bounds_c) - 1
    zone_ev = [[0.0] * nzc for _ in range(len(bounds_r) - 1)]
    for z in zones:
        zone_ev[z["row"]][z["col"]] = z["vote_ev"]

    curve = curve_params(stats, cfg)
    digest = output_digest(display)
    invariant = hashlib.sha256(
        (curve_key(curve) + "|" + digest).encode("utf-8")).hexdigest()

    result = {
        "ev": stats["metering_ev"],
        "out": out_rows,
        "zone_ev": zone_ev,
        "digest": digest,
        "stats": stats,
        "zones": zones,
        "curve": curve,
        "invariant": invariant,
        "size": [h, w],
    }
    if rgb_out is not None:
        result["rgb"] = rgb_out
    return result


def _recombine(display, y, rgbinfo, h, w, cfg):
    """Recombine the tone mapped luma with the pixel's original chromaticity.

    The colour layer is the input triple divided by its own luma (see
    split_luma_chroma), so:

        rgb_out = display_luma * (r/Y, g/Y, b/Y)

    reproduces the input colour exactly whenever the new luma fits in gamut,
    with the brightness replaced by the tone mapped value.

    Consequences worth stating plainly:

      * if the result is inside [0, 1]^3, the luma equals the tone mapped
        value and the colour ratios equal the input's, exactly;
      * if it is outside, something must give.  The pipeline keeps the luma
        (the authoritative brightness) and desaturates towards the pixel's
        grey point, because clipping the peak channel alone would shift the
        hue and dimming the triple would undo the exposure decision.  The
        out-of-gamut case is reported by tests/test_invariance.py.
    """
    chroma = split_luma_chroma(y, rgbinfo, h, w)
    if chroma is None:
        return None
    sat = cfg["saturation"]
    out = []
    for r in range(h):
        row = []
        base = r * w
        for c in range(w):
            v = display[base + c]
            i = base + c
            ratio = chroma[i]
            if ratio[0] <= 0.0 and ratio[1] <= 0.0 and ratio[2] <= 0.0:
                # a pure black pixel has no chroma to carry
                row.append([0.0, 0.0, 0.0])
                continue
            if sat != 1.0:
                # move the chroma towards / away from the pixel's own grey
                # axis, then renormalise so the triple's luma is still 1: a
                # pure chroma knob must not change the tone mapped brightness
                lum = 0.2126 * ratio[0] + 0.7152 * ratio[1] + 0.0722 * ratio[2]
                if lum > 0.0:
                    ratio = tuple(max(0.0, lum + (q - lum) * sat)
                                  for q in ratio)
                    new_lum = (0.2126 * ratio[0] + 0.7152 * ratio[1] +
                               0.0722 * ratio[2])
                    if new_lum > 0.0:
                        ratio = tuple(q * (lum / new_lum) for q in ratio)
            rgb = [v * q for q in ratio]
            # In gamut?  Then the luma is exactly what the tone map chose and
            # the colour ratios are exactly the input's: nothing to do.
            peak = max(rgb)
            if peak > 1.0:
                # Out of gamut.  Desaturating towards the pixel's grey point
                # keeps the tone mapped luma (the authoritative brightness)
                # and avoids the hue shift of per channel clipping.
                grey = v
                t = (peak - 1.0) / (peak - grey) if peak > grey else 1.0
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                rgb = [q + (grey - q) * t for q in rgb]
            row.append([_clamp01(rgb[0]), _clamp01(rgb[1]), _clamp01(rgb[2])])
        out.append(row)
    return out


def _clamp01(v):
    if v != v:
        return 0.0
    return 0.0 if v <= 0.0 else (1.0 if v >= 1.0 else v)


def target_display(y_value, result):
    """Display value the run's own curve predicts for a linear luma."""
    curve = result["curve"]
    cfg = {
        "pivot": curve["pivot"],
        "lift_k": curve["lift_k"],
        "gamma": curve["gamma"],
        "detail_gain": curve["detail_gain"],
    }
    stats = {
        "metering_ev": result["ev"],
        "log_key": curve["log_key"],
        "lift_cap_ev": curve["shadow_gain_cap_ev"],
    }
    return tone.predict_display(y_value, cfg, stats)


TARGET_DISPLAY = tone.target


def run_file(req_path, out_path):
    with open(req_path, "r") as fh:
        req = json.load(fh)
    base_dir = os.path.dirname(os.path.abspath(req_path)) or "."
    res = run(req, base_dir=base_dir)
    with open(out_path, "w") as fh:
        json.dump(res, fh, allow_nan=False)
    return res
