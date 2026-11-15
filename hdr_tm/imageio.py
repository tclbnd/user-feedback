"""Loading, validation, luminance/chroma separation and zone partitioning."""
import json
import math
import os

from .config import BLACK_EPS, LIN_MIN


# ---------------------------------------------------------------- loading --
def _shape_of(img):
    if not isinstance(img, list) or not img:
        raise ValueError("hdr must be a non-empty list")
    first = img[0]
    if isinstance(first, list) and first and isinstance(first[0], list):
        h = len(img)
        w = len(first)
        c = len(first[0])
        if c not in (1, 3, 4):
            raise ValueError("colour hdr must have 1, 3 or 4 channels")
        for r, row in enumerate(img):
            if not isinstance(row, list) or len(row) != w:
                raise ValueError("ragged hdr at row %d (expected %d cols)" % (r, w))
            for j, px in enumerate(row):
                if not isinstance(px, list) or len(px) != c:
                    raise ValueError("ragged hdr at (%d,%d) needs %d ch" % (r, j, c))
        return h, w, c
    h = len(img)
    w = len(first)
    if w == 0:
        raise ValueError("hdr rows must be non-empty")
    for r, row in enumerate(img):
        if not isinstance(row, list) or len(row) != w:
            raise ValueError("ragged hdr at row %d (expected %d cols)" % (r, w))
        for j, v in enumerate(row):
            if isinstance(v, list):
                raise ValueError("mixed 2-D/3-D hdr at (%d,%d)" % (r, j))
    return h, w, 0


def _finite(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("non numeric sample at %s: %r" % (where, v))
    v = float(v)
    if not math.isfinite(v):
        raise ValueError("non finite sample at %s: %r" % (where, v))
    return v


def resolve_ref(ref, base_dir="."):
    """hdr may be an inline array or a path to a JSON file holding one."""
    if isinstance(ref, str):
        path = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
        with open(path, "r") as fh:
            return json.load(fh)
    return ref


def load(ref, base_dir="."):
    """Return (y, rgb_planes, h, w).

    y   : linear Rec.709 luma per row, clamped to >= 0
    rgb : None for 2-D input, else ((r,g,b) flat planes, channel_count)
    """
    data = resolve_ref(ref, base_dir)
    h, w, c = _shape_of(data)
    if c == 0:
        y = [[max(0.0, _finite(v, "(%d,%d)" % (r, j))) for j, v in enumerate(row)]
             for r, row in enumerate(data)]
        return y, None, h, w
    planes = ([], [], [])
    y = []
    for r, row in enumerate(data):
        yr = []
        for j, px in enumerate(row):
            vals = [_finite(v, "(%d,%d)" % (r, j)) for v in px]
            if c == 1:
                rgb = (vals[0], vals[0], vals[0])
            else:
                rgb = (vals[0], vals[1], vals[2])
            for k in range(3):
                planes[k].append(max(0.0, rgb[k]))
            yr.append(max(0.0, 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]))
        y.append(yr)
    n = h * w
    rgbp = tuple(p[:n] for p in planes)
    return y, (rgbp, c), h, w


def split_luma_chroma(y, rgbinfo, h, w):
    """Brightness / colour separation.

    Returns a flat list of (r/Y, g/Y, b/Y) per pixel, or None for achromatic
    input.  The triple has luma exactly 1 by construction (it is the input
    colour divided by its own luma), so re-multiplying by any new luma keeps
    the original chromaticity: brightness and colour are cleanly separated.

    A pixel with no measurable luma but non-zero colour falls back to ratios
    against its brightest channel, and a pure black pixel maps to (0, 0, 0).
    """
    if rgbinfo is None:
        return None
    rs, gs, bs = rgbinfo[0]
    chroma = [(0.0, 0.0, 0.0)] * (h * w)
    for i in range(h * w):
        r, g, b = rs[i], gs[i], bs[i]
        yr = y[i // w][i % w]
        if yr > BLACK_EPS:
            chroma[i] = (r / yr, g / yr, b / yr)
            continue
        m = r if r > g else g
        m = m if m > b else b
        if m > 0.0:
            chroma[i] = (r / m, g / m, b / m)
    return chroma


# ---------------------------------------------------------------- zones ----
def zone_grid(h, w, tile, zones=None):
    """Partition the frame; returns inclusive start / exclusive stop bounds."""
    if zones is not None:
        nr = min(int(zones[0]), h)
        nc = min(int(zones[1]), w)
    else:
        nr = max(1, (h + tile - 1) // tile)
        nc = max(1, (w + tile - 1) // tile)
    bounds_r = [(i * h) // nr for i in range(nr + 1)]
    bounds_c = [(j * w) // nc for j in range(nc + 1)]
    return bounds_r, bounds_c


def iter_zones(bounds_r, bounds_c, order=None):
    """Yield (zone_index, r0, r1, c0, c1) in the requested visit order."""
    nr = len(bounds_r) - 1
    nc = len(bounds_c) - 1
    idx = list(range(nr * nc))
    if order is not None:
        idx = [idx[k] for k in order]
    for z in idx:
        zi, zj = divmod(z, nc)
        yield z, bounds_r[zi], bounds_r[zi + 1], bounds_c[zj], bounds_c[zj + 1]


def check_partition(bounds_r, bounds_c, h, w):
    cov = 0
    for zi in range(len(bounds_r) - 1):
        for zj in range(len(bounds_c) - 1):
            cov += ((bounds_r[zi + 1] - bounds_r[zi]) *
                    (bounds_c[zj + 1] - bounds_c[zj]))
    if cov != h * w:
        raise AssertionError("zone partition lost pixels: %d != %d" % (cov, h * w))


def shuffle_order(n_zones, seed):
    """Deterministic permutation of the zone visit order."""
    if not seed:
        return None
    idx = list(range(n_zones))
    # xorshift32 driven Fisher-Yates: deterministic, platform independent.
    s = (int(seed) & 0xFFFFFFFF) or 0x9E3779B9
    for i in range(n_zones - 1, 0, -1):
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        s &= 0xFFFFFFFF
        j = s % (i + 1)
        idx[i], idx[j] = idx[j], idx[i]
    return idx


def safe_luma(y, h, w):
    """Luma re-clamped to a safe linear floor (used by the tone stage)."""
    return [[max(LIN_MIN, v) for v in row] for row in y]
