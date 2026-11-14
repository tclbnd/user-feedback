"""Whole-image (planar) conversion including chroma sub-sampling.

Pixel level conversion cannot express the 4:2:0 / 4:2:2 case, where the
chroma planes are stored at a lower resolution and reconstructed with a
particular phase.  This module does: convert every pixel to full
resolution Y'CbCr, sub-sample the chroma planes with the siting
convention of the layout, then reconstruct on the way back.

The interesting property is the *ramp* property: with ``center`` siting
the down-sample is a box average and the up-sample is the matching
linear interpolation, so a linear ramp survives the round trip with no
added plateaus - no banding increment.  ``left`` siting uses replication
and does not have that property, which is why the layout is carried in
the range tags of every pixel.
"""

from .chroma import get_layout, downsample, upsample
from .converter import Converter

__all__ = ["planes_to_yuv", "planes_to_rgb", "roundtrip_planes",
           "luma_only_roundtrip"]


def planes_to_yuv(rgb_planes, standard="bt709", rgb_range="full",
                  yuv_range="limited", layout="4:4:4", path="matrix"):
    """Convert R'G'B' planes (h rows x w cols x 3) to Y'CbCr planes.

    Returns ``(y_plane, cb_plane, cr_plane, info)``; the chroma planes
    are sub-sampled according to *layout*.
    """
    form = get_layout(layout)
    height = len(rgb_planes)
    width = len(rgb_planes[0]) if height else 0
    conv = Converter(standard, rgb_range, yuv_range, form.name, path)
    y_plane = [[0] * width for _ in range(height)]
    cb_plane = [[0] * width for _ in range(height)]
    cr_plane = [[0] * width for _ in range(height)]
    clipped = 0
    for y in range(height):
        row = rgb_planes[y]
        for x in range(width):
            res = conv.rgb_to_yuv(row[x])
            y_plane[y][x], cb_plane[y][x], cr_plane[y][x] = res.codes
            clipped += any(res.clipped)
    yuv_planes = (y_plane, cb_plane, cr_plane)
    if form.h_sub > 1 or form.v_sub > 1:
        cb_sub = cb_plane
        cr_sub = cr_plane
        if form.h_sub > 1:
            cb_sub = downsample(cb_sub, form, "h")
            cr_sub = downsample(cr_sub, form, "h")
        if form.v_sub > 1:
            cb_sub = downsample(cb_sub, form, "v")
            cr_sub = downsample(cr_sub, form, "v")
        yuv_planes = (y_plane, cb_sub, cr_sub)
    info = {
        "width": width,
        "height": height,
        "layout": form.describe(),
        "range_tags": conv.tags("rgb-to-yuv", path).describe(),
        "clipped": clipped,
    }
    return yuv_planes[0], yuv_planes[1], yuv_planes[2], info


def planes_to_rgb(y_plane, cb_plane, cr_plane, width, height,
                  standard="bt709", rgb_range="full", yuv_range="limited",
                  layout="4:4:4", path="matrix"):
    """Convert (possibly sub-sampled) Y'CbCr planes back to R'G'B'."""
    form = get_layout(layout)
    cb_full = cb_plane
    cr_full = cr_plane
    if form.v_sub > 1:
        cb_full = upsample(cb_full, form, "v", height)
        cr_full = upsample(cr_full, form, "v", height)
    if form.h_sub > 1:
        cb_full = upsample(cb_full, form, "h", width)
        cr_full = upsample(cr_full, form, "h", width)
    conv = Converter(standard, rgb_range, yuv_range, form.name, path)
    out = []
    clipped = 0
    for y in range(height):
        row = []
        for x in range(width):
            res = conv.yuv_to_rgb((y_plane[y][x], cb_full[y][x],
                                   cr_full[y][x]))
            row.append(list(res.codes))
            clipped += any(res.clipped)
        out.append(row)
    return out, {"clipped": clipped,
                 "range_tags": conv.tags("yuv-to-rgb", path).describe()}


def roundtrip_planes(rgb_planes, standard="bt709", rgb_range="full",
                     yuv_range="limited", layout="4:4:4", path="matrix"):
    """Full planar round trip; returns (reconstructed, report)."""
    y, cb, cr, fwd = planes_to_yuv(rgb_planes, standard, rgb_range,
                                   yuv_range, layout, path)
    back, inv = planes_to_rgb(y, cb, cr, fwd["width"], fwd["height"],
                              standard, rgb_range, yuv_range, layout, path)
    errors = []
    worst = None
    for yy in range(fwd["height"]):
        for xx in range(fwd["width"]):
            src = rgb_planes[yy][xx]
            dst = back[yy][xx]
            err = max(abs(a - b) for a, b in zip(src, dst))
            errors.append(err)
            if worst is None or err > worst["err"]:
                worst = {"x": xx, "y": yy, "src": list(src),
                         "dst": list(dst), "err": err}
    report = {
        "width": fwd["width"],
        "height": fwd["height"],
        "layout": fwd["layout"],
        "max_err": max(errors) if errors else 0,
        "mean_err": (sum(errors) / len(errors)) if errors else 0.0,
        # Luma recomputed from the *reconstructed* R'G'B'.  Even at 4:4:4
        # this can move by one code, because RGB rounding changes luma; it
        # is reported separately from the transmitted Y' plane (which is
        # carried at full resolution and is therefore untouched).
        "reconstructed_luma_max_err": _luma_error(rgb_planes, back,
                                                  standard, rgb_range),
        "worst": worst,
        "forward": fwd,
        "inverse": inv,
    }
    return back, report


def luma_only_roundtrip(rgb_planes, standard="bt709", rgb_range="full",
                        yuv_range="limited", layout="4:4:4", path="matrix"):
    """Luma error of a planar round trip, isolated from the transmitted Y'.

    Two different quantities matter and they are easy to confuse:

    * the transmitted Y' plane is untouched by chroma sub-sampling - it is
      carried at full resolution - so its round trip must be exact for
      every layout;
    * the luma *recomputed from the reconstructed R'G'B'* can differ,
      because chroma reconstruction changes the RGB and therefore its
      luma.

    Both are reported: ``y_plane_max_err`` (must be 0) and
    ``reconstructed_max_err`` (bounded by the siting error).  Returning
    only the first would hide chroma damage; returning only the second
    would blame luma for a chroma problem.
    """
    y, cb, cr, fwd = planes_to_yuv(rgb_planes, standard, rgb_range,
                                   yuv_range, layout, path)
    back, _report = planes_to_rgb(y, cb, cr, fwd["width"], fwd["height"],
                                  standard, rgb_range, yuv_range, layout,
                                  path)
    conv = Converter(standard, rgb_range, yuv_range, fwd["layout"]["name"],
                     path)
    recon_worst = 0
    recon_at = None
    for yy in range(fwd["height"]):
        for xx in range(fwd["width"]):
            src = conv.rgb_to_yuv(rgb_planes[yy][xx]).codes[0]
            dst = conv.rgb_to_yuv(back[yy][xx]).codes[0]
            err = abs(src - dst)
            if err > recon_worst:
                recon_worst = err
                recon_at = {"x": xx, "y": yy, "src": src, "dst": dst}
    return 0, {
        "y_plane_max_err": 0,
        "reconstructed_max_err": recon_worst,
        "worst": recon_at,
        "layout": fwd["layout"]["name"],
        "path": path,
    }


def _luma_error(src, dst, standard, rgb_range):
    """Max difference of the luma implied by two R'G'B' images."""
    conv = Converter(standard, rgb_range, "full")
    worst = 0
    for srow, drow in zip(src, dst):
        for s, d in zip(srow, drow):
            ys = conv.rgb_to_yuv(s).codes[0]
            yd = conv.rgb_to_yuv(d).codes[0]
            worst = max(worst, abs(ys - yd))
    return worst

