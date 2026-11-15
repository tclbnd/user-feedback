#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiled, depth-sorted anisotropic Gaussian splatting.

Usage:
    python3 splat.py input.json output.json

Model (see README.md)
---------------------
A primitive carries a screen-space 2D covariance

    Sigma = [[sx, sxy], [sxy, sy]]

and its kernel is the analytic Gaussian

    G_i(p) = exp(-0.5 * d^T Sigma^-1 d),   d = p - center_i

evaluated at the pixel centre p.  The 3-sigma coverage box of a primitive is
binned into every tile it overlaps, so a tile holds the full list of
primitives that may touch it.  Each tile sorts that list by a total depth key
and composites front to back:

    out(p) = sum_i  a_i G_i(p) T_i(p) rgb_i
    T_i(p) = prod_{j before i} (1 - a_j G_j(p))

A pixel is only ever written by primitives whose own 3-sigma pixel box
contains it, independently of which tile the pixel belongs to.  The Gaussian
is therefore never clipped at a tile edge: both sides of a seam see exactly
the same primitive set, and the frame is bit-identical for any tile size.

Determinism
-----------
The input order must not matter.  Primitives are first put in a canonical
total order: depth z first (front to back), then the full content tuple
(x, y, sx, sy, sxy, a, r, g, b).  Hence
  * permuting the input cannot change a single output bit, and
  * primitives with equal depth are ordered by content, i.e. reproducibly,
    instead of flickering with insertion / hash order.

Output
------
{"pixels":       [[r, g, b], ...]  row major, length w*h,
 "tile_counts":  [[#primitives binned in tile], ...],
 "pixel_counts": [#primitives contributing to pixel, ...] row major,
 "digest":       sha1 of the exact IEEE-754 pixel values}

`digest` covers w, h and the raw doubles only, so it is invariant to the
input primitive order and to the tile size.
"""

import hashlib
import json
import math
import struct
import sys

# Bump only if the numerical definition of the frame changes.
_DIGEST_TAG = b"gaussian-splat/tiled-depth-v1\n"
_COVERAGE_SIGMA = 3.0


def _finite(*values):
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if not math.isfinite(float(v)):
            return False
    return True


def _make_primitive(raw):
    """Validate one primitive and precompute per-primitive raster data.

    Returns None for malformed primitives and for covariances that are not
    positive definite; such a primitive has no well defined kernel and
    contributes nothing.
    """
    try:
        x = float(raw["x"])
        y = float(raw["y"])
        z = float(raw.get("z", 0.0))
        sx = float(raw["sx"])
        sy = float(raw["sy"])
        sxy = float(raw.get("sxy", 0.0))
        a = float(raw["a"])
        rgb = raw["rgb"]
        r = float(rgb[0])
        g = float(rgb[1])
        b = float(rgb[2])
    except (KeyError, TypeError, ValueError, IndexError):
        return None

    if not _finite(x, y, z, sx, sy, sxy, a, r, g, b):
        return None

    det = sx * sy - sxy * sxy
    if not (sx > 0.0 and sy > 0.0 and det > 0.0):
        return None

    inv_xx = sy / det
    inv_xy = -sxy / det
    inv_yy = sx / det

    # 3-sigma support: half extent along axis u is 3*sqrt(variance of u).
    # The larger axis extent bounds the ellipse conservatively in both axes.
    radius = _COVERAGE_SIGMA * math.sqrt(max(sx, sy))
    if not math.isfinite(radius):
        return None

    # Guard the box arithmetic against overflow for absurd coordinates.
    lo_x, hi_x = x - radius, x + radius
    lo_y, hi_y = y - radius, y + radius
    if not (_finite(lo_x, hi_x, lo_y, hi_y)):
        if not all(math.isfinite(v) for v in (x, y)):
            return None
        lo_x = hi_x = x
        lo_y = hi_y = y

    return {
        "x": x, "y": y, "z": z,
        "sx": sx, "sy": sy, "sxy": sxy,
        "a": a, "r": r, "g": g, "b": b,
        "ixx": inv_xx, "ixy": inv_xy, "iyy": inv_yy,
        "rad": radius,
        # Pixel indices whose centres lie inside the 3-sigma box.  Clamped to
        # the image in render(); these bounds do not depend on the tiling.
        "px0": max(0, int(math.ceil(lo_x - 0.5))),
        "px1": int(math.floor(hi_x - 0.5)),
        "py0": max(0, int(math.ceil(lo_y - 0.5))),
        "py1": int(math.floor(hi_y - 0.5)),
    }


def _order_key(p):
    """Canonical total order: depth first, then content (input-order free)."""
    return (p["z"], p["x"], p["y"],
            p["sx"], p["sy"], p["sxy"],
            p["a"], p["r"], p["g"], p["b"])


def render(req):
    """Render one request dict and return the result dict."""
    try:
        W = int(req["w"])
        H = int(req["h"])
        T = int(req.get("tile", 8))
    except (KeyError, TypeError, ValueError):
        raise ValueError("request needs integer w, h and an optional integer tile")
    if W <= 0 or H <= 0:
        raise ValueError("w and h must be positive")
    if T <= 0:
        raise ValueError("tile must be positive")

    prims = []
    for raw in req.get("prims", []):
        p = _make_primitive(raw)
        if p is not None:
            prims.append(p)

    # Canonical order, established before any tiling or sorting happens:
    # this is what makes the frame invariant under permutation of the input.
    prims.sort(key=_order_key)
    keys = [_order_key(p) for p in prims]

    ntx = (W + T - 1) // T
    nty = (H + T - 1) // T

    # ---- tile binning: every tile overlapped by the 3-sigma box gets the
    #      primitive, including tiles only grazed by it ---------------------
    tiles = [[[] for _ in range(ntx)] for _ in range(nty)]
    for pi, p in enumerate(prims):
        x, y, rad = p["x"], p["y"], p["rad"]
        tx0 = max(0, int(math.floor((x - rad) / T)))
        tx1 = min(ntx - 1, int(math.floor((x + rad) / T)))
        ty0 = max(0, int(math.floor((y - rad) / T)))
        ty1 = min(nty - 1, int(math.floor((y + rad) / T)))
        p["px1"] = min(W - 1, p["px1"])
        p["py1"] = min(H - 1, p["py1"])
        for ty in range(ty0, ty1 + 1):
            row = tiles[ty]
            for tx in range(tx0, tx1 + 1):
                row[tx].append(pi)

    tile_counts = [[len(tiles[ty][tx]) for tx in range(ntx)]
                   for ty in range(nty)]
    pixel_counts = [0] * (W * H)

    # ---- per-pixel front-to-back alpha compositing ------------------------
    n = W * H
    out_r = [0.0] * n
    out_g = [0.0] * n
    out_b = [0.0] * n
    trans = [1.0] * n

    exp = math.exp

    for ty in range(nty):
        y0 = ty * T
        y1 = min(H, y0 + T)
        for tx in range(ntx):
            bucket = tiles[ty][tx]
            if not bucket:
                continue
            # Stable sort by the depth key.  The bucket was built from the
            # globally sorted list, so this is already ordered; sorting again
            # makes the "sorted per tile" rule explicit and robust.
            bucket.sort(key=keys.__getitem__)

            x0 = tx * T
            x1 = min(W, x0 + T)

            for pi in bucket:
                p = prims[pi]
                # Intersect the primitive's own support with the tile: the
                # primitive itself defines *which* pixels it touches, the
                # tile only bounds the loop.  No Gaussian is truncated here.
                px0 = x0 if p["px0"] < x0 else p["px0"]
                px1 = (x1 - 1) if p["px1"] > (x1 - 1) else p["px1"]
                py0 = y0 if p["py0"] < y0 else p["py0"]
                py1 = (y1 - 1) if p["py1"] > (y1 - 1) else p["py1"]
                if px0 > px1 or py0 > py1:
                    continue

                cx = p["x"]
                cy = p["y"]
                ixx, ixy, iyy = p["ixx"], p["ixy"], p["iyy"]
                a = p["a"]
                cr, cg, cb = p["r"], p["g"], p["b"]

                for yy in range(py0, py1 + 1):
                    dy = yy + 0.5 - cy
                    dy2 = dy * dy
                    row_off = yy * W
                    for xx in range(px0, px1 + 1):
                        dx = xx + 0.5 - cx
                        g = exp(-0.5 * (ixx * dx * dx
                                        + 2.0 * ixy * dx * dy
                                        + iyy * dy2))
                        aa = a * g
                        idx = row_off + xx
                        t = trans[idx]
                        out_r[idx] += cr * aa * t
                        out_g[idx] += cg * aa * t
                        out_b[idx] += cb * aa * t
                        trans[idx] = t * (1.0 - aa)
                        pixel_counts[idx] += 1

    pixels = [[out_r[i], out_g[i], out_b[i]] for i in range(n)]

    # ---- frame digest: exact IEEE-754 doubles, no rounding ---------------
    h = hashlib.sha1()
    h.update(_DIGEST_TAG)
    h.update(struct.pack("<II", W, H))
    buf = bytearray(24 * n)
    off = 0
    for i in range(n):
        struct.pack_into("<3d", buf, off, out_r[i], out_g[i], out_b[i])
        off += 24
    h.update(buf)

    return {"pixels": pixels,
            "tile_counts": tile_counts,
            "pixel_counts": pixel_counts,
            "digest": h.hexdigest()}


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 splat.py input.json output.json\n")
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        req = json.load(fh)
    res = render(req)
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(res, fh, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
