"""Measurement helpers: round-trip error, banding, boundary analysis.

These are the numbers the acceptance criteria are stated in, so they
live in the library rather than in a test file:

``roundtrip_stats``   per-channel max/mean error over many pixels
``ramp``              synthetic grey and colour ramps
``banding_profile``   counts of identical successive steps and of the
                      largest plateau in a 1-D signal; banding is the
                      presence of *extra* flat runs compared with the
                      ideal ramp, so the two are reported together
``boundary_analysis`` how close the exact result sits to a rounding
                      boundary, which is where two correct
                      implementations may legitimately differ by 1
"""

import math
import random
from fractions import Fraction

__all__ = [
    "roundtrip_stats",
    "ramp",
    "grey_ramp",
    "banding_profile",
    "boundary_analysis",
    "random_pixels",
    "random_yuv_pixels",
    "boundary_samples",
    "error_histogram",
]


def random_pixels(count, rgb_range="full", seed=0, standard_pixels=True):
    """Deterministic pseudo-random R'G'B' pixels within *rgb_range*."""
    rng = random.Random(seed)
    lo, hi = (0, 255) if rgb_range == "full" else (16, 235)
    return [[rng.randint(lo, hi) for _ in range(3)] for _ in range(count)]


def random_yuv_pixels(count, yuv_range="limited", seed=0):
    rng = random.Random(seed)
    if yuv_range == "full":
        ylo, yhi = 0, 255
        clo, chi = 0, 255
    else:
        ylo, yhi = 16, 235
        clo, chi = 16, 240
    return [[rng.randint(ylo, yhi), rng.randint(clo, chi), rng.randint(clo, chi)]
            for _ in range(count)]


def boundary_samples(standard="bt709", rgb_range="full", yuv_range="limited"):
    """The extreme and neutral colours that bracket the whole cube."""
    lo = 0 if rgb_range == "full" else 16
    hi = 255 if rgb_range == "full" else 235
    mid = 128 if rgb_range == "full" else 126
    colours = [
        ("black", (lo, lo, lo)),
        ("white", (hi, hi, hi)),
        ("red", (hi, lo, lo)),
        ("green", (lo, hi, lo)),
        ("blue", (lo, lo, hi)),
        ("cyan", (lo, hi, hi)),
        ("magenta", (hi, lo, hi)),
        ("yellow", (hi, hi, lo)),
        ("grey50", (mid, mid, mid)),
        ("grey25", ((lo * 3 + hi) // 4,) * 3),
        ("grey75", ((lo + hi * 3) // 4,) * 3),
        ("dark-red", (lo + 1, lo, lo)),
        ("bright-blue", (lo, lo, hi - 1)),
    ]
    return colours


def roundtrip_stats(converter, pixels, direction="rgb-to-yuv"):
    """Convert *pixels* and back, returning error statistics."""
    back_dir = ("yuv-to-rgb" if direction == "rgb-to-yuv" else "rgb-to-yuv")
    per_channel = []
    errors = []
    worst = None
    clipped = 0
    for idx, pixel in enumerate(pixels):
        mid = converter.convert(pixel, direction)
        back = converter.convert(mid.codes, back_dir)
        deltas = [abs(a - b) for a, b in zip(pixel, back.codes)]
        if per_channel and len(per_channel) == len(deltas):
            per_channel = [max(a, b) for a, b in zip(per_channel, deltas)]
        else:
            per_channel = deltas
        err = max(deltas)
        errors.append(err)
        if any(mid.clipped):
            clipped += 1
        if worst is None or err > worst["err"]:
            worst = {"index": idx, "src": list(pixel),
                     "yuv": list(mid.codes), "back": list(back.codes),
                     "err": err}
    n = max(1, len(errors))
    return {
        "pixels": len(errors),
        "max_err": max(errors) if errors else 0,
        "mean_err": sum(errors) / n,
        "per_channel_max": per_channel,
        "worst": worst,
        "clipped_pixels": clipped,
        "tolerance": 2.0,
        "ok": (max(errors) if errors else 0) <= 2,
        "histogram": error_histogram(errors),
    }


def error_histogram(errors):
    hist = {}
    for e in errors:
        hist[e] = hist.get(e, 0) + 1
    return {str(k): hist[k] for k in sorted(hist)}


def grey_ramp(width=256, rgb_range="full"):
    """A neutral ramp; in limited range it spans 16..235."""
    lo, hi = (0, 255) if rgb_range == "full" else (16, 235)
    out = []
    for x in range(width):
        if width == 1:
            v = lo
        else:
            v = Fraction(x, width - 1) * (hi - lo) + lo
        code = int(math.floor(v + Fraction(1, 2)))
        out.append((code, code, code))
    return out


def ramp(width=256, start=(0, 0, 0), end=(255, 255, 255)):
    """A linear ramp between two RGB corners, rounded half up."""
    out = []
    for x in range(width):
        t = Fraction(x, width - 1) if width > 1 else Fraction(0)
        out.append(tuple(int(math.floor(Fraction(s) + (Fraction(e) - s) * t
                                    + Fraction(1, 2)))
                          for s, e in zip(start, end)))
    return out


def banding_profile(values):
    """Describe the staircase structure of a 1-D integer signal.

    Reported quantities:

    ``levels``          distinct values used
    ``stalls``          adjacent pairs with equal values; for a monotone
                        ramp this is exactly ``len - levels``
    ``plateaus``        maximal runs of length >= 2 (a plateau is a run
                        the eye reads as a flat step)
    ``max_run``         longest identical run
    ``reversals``       direction changes; a ramp must have none
    ``total_variation`` sum of |delta|; a monotone ramp keeps the source
                        variation when no level is skipped or repeated
                        beyond quantisation

    ``stalls - (len - levels)`` isolates *non-quantisation* stalls: on a
    cleanly quantised monotone ramp it is zero, and banding introduced by
    a bad range conversion shows up as a large surplus.
    """
    values = list(values)
    if not values:
        return {"length": 0, "levels": 0, "stalls": 0, "plateaus": 0,
                "max_run": 0, "reversals": 0, "total_variation": 0,
                "excess_stalls": 0}
    levels = len(set(values))
    stalls = 0
    plateaus = 0
    max_run = 1
    run = 1
    reversals = 0
    direction = 0
    tv = 0
    for prev, cur in zip(values, values[1:]):
        delta = cur - prev
        tv += abs(delta)
        if delta == 0:
            stalls += 1
            run += 1
        else:
            if run > max_run:
                max_run = run
            if run >= 2:
                plateaus += 1
            run = 1
        sign = (delta > 0) - (delta < 0)
        if sign and direction and sign != direction:
            reversals += 1
        if sign:
            direction = sign
    max_run = max(max_run, run)
    if run >= 2:
        plateaus += 1
    return {
        "length": len(values),
        "levels": levels,
        "stalls": stalls,
        "plateaus": plateaus,
        "max_run": max_run,
        "reversals": reversals,
        "total_variation": tv,
        "excess_stalls": stalls - (len(values) - levels),
    }


def boundary_analysis(result):
    """Aggregate the distance-to-boundary of unrounded raw codes."""
    import statistics
    dists = [r.boundary_distance() for r in result.raw]
    return {
        "raw": [float(r.value) for r in result.raw],
        "clipped": list(result.clipped),
        "min_dist": min(dists),
        "max_dist": max(dists),
        "mean_dist": statistics.fmean(dists),
        "near_boundary": min(dists) < 1e-9,
    }

