"""What "lossless within tolerance" means for each channel.

Rounding to 8-bit samples in the middle of a round trip is inherently
lossy, so "lossless" has to be stated as a *bound* per channel rather
than as an equality.  The bounds are derived analytically from the
constants actually in use (never hard coded per standard), so the claim
and the matrices can never drift apart:

    RGB -> YUV -> RGB
        the only loss is the 1/2 code rounding of Y, Cb and Cr, so

        dR = 1/2 * (rgb_span/y_span) + 1/2 * (rgb_span/c_span) * 2(1-Kr)
        dB = 1/2 * (rgb_span/y_span) + 1/2 * (rgb_span/c_span) * 2(1-Kb)
        dG =          (rgb_span/c_span) * (Kr(1-Kr) + Kb(1-Kb)) / Kg ... etc.

    YUV -> RGB -> YUV
        the bound holds for *in gamut* triples.  Outside the gamut the
        first leg clips R'G'B' to the declared range, and no round trip
        can recover that - which is exactly why every pixel carries a
        ``clipped`` flag rather than silently looking fine.

Clipping rules (also enforced in :mod:`yuvkit.matrix`):

* an intermediate outside the declared range is clipped to that range's
  end point and flagged (extreme colours, out-of-gamut Y'CbCr),
* a negative R'G'B' intermediate is clipped to the range minimum and
  additionally flagged ``negative-*`` so the *content* problem survives
  the clamp,
* rounding is half-up and happens *after* clipping is computed from the
  unrounded value, so "clipped" is always about the mathematical result.
"""

from fractions import Fraction

from .matrix import DEN, get_matrix
from .ranges import get_range

__all__ = [
    "TOLERANCE",
    "CHANNEL_BOUNDS",
    "bounds_rgb_to_yuv_roundtrip",
    "bounds_yuv_to_rgb_roundtrip",
    "lossless_channels",
    "classify_roundtrip",
    "clipping_rules",
]

#: The acceptance budget in code units (2/255 of full scale).
TOLERANCE = Fraction(2, 255)

CHANNEL_BOUNDS = {
    "R": "luma plus Cr reconstruction; the Cr step weights 2(1-Kr)",
    "G": "weighted mix of Cb and Cr; no direct luma term survives",
    "B": "luma plus Cb reconstruction; the Cb step weights 2(1-Kb)",
}


def bounds_rgb_to_yuv_roundtrip(standard, rgb_range, yuv_range):
    """Analytic worst case for RGB -> YUV -> RGB, per channel."""
    m = get_matrix(standard)
    rgb = get_range(rgb_range)
    yuv = get_range(yuv_range)
    rs = Fraction(rgb.rgb_max - rgb.rgb_min)
    y_step = rs / yuv.y_span          # RGB codes per luma code
    c_step = rs / yuv.c_span          # RGB codes per chroma code
    half = Fraction(1, 2)
    kr = m.krn / DEN
    kb = m.kbn / DEN
    kg = m.kgn / DEN
    # R' = Y' + 2(1-Kr)Cr, B' = Y' + 2(1-Kb)Cb
    r = half * y_step + half * c_step * 2 * (1 - kr) + half
    b = half * y_step + half * c_step * 2 * (1 - kb) + half
    # G' = Y' - (Kr/Kg)(R'-Y') - (Kb/Kg)(B'-Y'), so the luma terms cancel
    g = half * c_step * (2 * kr * (1 - kr) / kg + 2 * kb * (1 - kb) / kg) + half
    return {"R": r, "G": g, "B": b}


def bounds_yuv_to_rgb_roundtrip(standard, rgb_range, yuv_range):
    """Analytic worst case for YUV -> RGB -> YUV (in gamut).

    Going back, only the RGB rounding contributes, but it spreads across
    all three Y'CbCr channels.
    """
    m = get_matrix(standard)
    rgb = get_range(rgb_range)
    yuv = get_range(yuv_range)
    rs = Fraction(rgb.rgb_max - rgb.rgb_min)
    y_step = yuv.y_span / rs
    c_step = yuv.c_span / rs
    half = Fraction(1, 2)
    kr = m.krn / DEN
    kb = m.kbn / DEN
    kg = m.kgn / DEN
    # dY' = Kr dR + Kg dG + Kb dB ; with dR,dG,dB each <= 1/2 after
    # reconstruction plus 1/2 from the final rounding
    one = half * 3  # 1/2 per primary channel composition
    luma = one * y_step + half
    # dCb = 1/(2(1-Kb)) * (dB - dY'); bound the magnitudes
    cb = (one + one) * half / (2 * (1 - kb)) * c_step + half
    cr = (one + one) * half / (2 * (1 - kr)) * c_step + half
    return {"Y": luma, "Cb": cb, "Cr": cr}


def lossless_channels(standard="bt709", rgb_range="full",
                      yuv_range="limited", direction="rgb-to-yuv"):
    """Channels whose round trip is guaranteed inside the tolerance."""
    if direction == "rgb-to-yuv":
        bounds = bounds_rgb_to_yuv_roundtrip(standard, rgb_range, yuv_range)
    else:
        bounds = bounds_yuv_to_rgb_roundtrip(standard, rgb_range, yuv_range)
    limit = TOLERANCE * 255
    return {
        "direction": direction,
        "standard": get_matrix(standard).standard.name,
        "tolerance": float(limit),
        "bounds": {k: float(v) for k, v in bounds.items()},
        "lossless": [k for k, v in sorted(bounds.items()) if v <= limit],
        "lossy": [k for k, v in sorted(bounds.items()) if v > limit],
        "notes": {k: CHANNEL_BOUNDS.get(k, "") for k in bounds},
    }


def classify_roundtrip(pixels, standard="bt709", rgb_range="full",
                       yuv_range="limited", direction="rgb-to-yuv",
                       layout="4:4:4"):
    """Empirically classify each channel of a round trip on *pixels*.

    Each channel of the *returned* space gets one of:

    ``exact``    never differed on this sample set
    ``bounded``  differed, but always inside the 2-code tolerance
    ``clipped``  the intermediate leg clipped, so the error is content,
                 not arithmetic - it cannot be fixed by a better inverse
    ``over``     exceeded the tolerance without any clipping: a real bug

    Y'CbCr -> R'G'B' -> Y'CbCr is only round-trip safe *in gamut*; the
    out-of-gamut tail is reported as ``clipped`` instead of being hidden
    behind an aggregate maximum.
    """
    from .converter import Converter

    conv = Converter(standard, rgb_range, yuv_range, layout, "matrix")
    back_dir = ("yuv-to-rgb" if direction == "rgb-to-yuv" else "rgb-to-yuv")
    names = ("R", "G", "B") if direction == "rgb-to-yuv" else ("Y", "Cb", "Cr")
    limit = float(TOLERANCE * 255)
    worst = {n: 0 for n in names}
    clipped_err = {n: 0 for n in names}
    clean_err = {n: 0 for n in names}
    for pixel in pixels:
        mid = conv.convert(pixel, direction)
        back = conv.convert(mid.codes, back_dir)
        any_clip = any(mid.clipped)
        for n, a, b in zip(names, pixel, back.codes):
            err = abs(a - b)
            worst[n] = max(worst[n], err)
            if any_clip:
                clipped_err[n] = max(clipped_err[n], err)
            else:
                clean_err[n] = max(clean_err[n], err)
    out = {}
    for n in names:
        if clean_err[n] > limit:
            out[n] = "over"
        elif worst[n] > clean_err[n] and clipped_err[n] > limit:
            out[n] = "clipped"
        elif worst[n] == 0:
            out[n] = "exact"
        elif worst[n] <= limit:
            out[n] = "bounded"
        else:
            out[n] = "over"
    return {
        "direction": direction,
        "worst": worst,
        "clean_max": clean_err,
        "clipped_max": clipped_err,
        "classes": out,
        "tolerance": limit,
        "ok": all(v != "over" for v in out.values()),
    }


def clipping_rules():
    """Machine-readable description of the truncation policy."""
    return {
        "rounding": "nearest, ties toward +infinity (half-up)",
        "clip_after_rounding_bound": True,
        "clip_to": "the declared range end point of the target space",
        "full_range_limits": [0, 255],
        "limited_range_limits_y": [16, 235],
        "limited_range_limits_c": [16, 240],
        "negative_intermediates": (
            "clipped to the range minimum and reported as negative-<channel>"),
        "out_of_gamut": (
            "flagged as clipped-<channel> together with the unrounded value"),
        "lossless_claim": (
            "round trip <= 2 codes for in-gamut content; out-of-gamut "
            "content is flagged rather than silently reported as clean"),
    }

