"""Signed-distance-field glyph atlas and coverage renderer.

Pipeline
--------
1. Expand every component's quadratic outline under its affine transform
   (transforming the three Bezier points is exact for affine maps), close
   contours implicitly, and keep shared endpoints exactly shared.
2. Pack per-glyph blocks into one atlas with the requested texel padding
   (plus room for the largest stroke reusing the glyph) and a gutter so
   blocks never overlap; the same block is reused for every render size.
3. Store the exact signed distance (em units, negative inside) at each
   texel centre, using the nonzero winding rule for the sign so holes and
   mirrored components behave and near-coincident edges stay spike-free.
4. Render by mapping pixel centres back to em space, bilinearly sampling
   the glyph's own block (clamped, so neighbouring blocks are never read),
   dilating by `stroke`, and applying a one-pixel-wide linear coverage
   ramp, which keeps the topology stable under zoom and sub-pixel shifts.

Usage: python3 glyph.py input.json output.json
"""
import argparse
import json
import math
from pathlib import Path

EPS = 1e-12
FAR = 1e6  # "infinite" distance used for empty glyphs / gutters
GUTTER = 1  # texels of dead space between atlas blocks


# --------------------------------------------------------------------------
# Polynomial roots
# --------------------------------------------------------------------------

def _cbrt(x):
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def quad_roots(a, b, c):
    """Real roots of a t^2 + b t + c = 0."""
    if abs(a) < EPS:
        if abs(b) < EPS:
            return []
        return [-c / b]
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return []
    if disc == 0.0:
        return [-b / (2.0 * a)]
    s = math.sqrt(disc)
    q = -0.5 * (b + math.copysign(s, b))
    r1 = q / a
    r2 = c / q if q != 0.0 else (-b - math.copysign(s, b)) / (2.0 * a)
    return [r1, r2]


def cubic_roots(a, b, c, d):
    """Real roots of a t^3 + b t^2 + c t + d = 0 (Newton-polished)."""
    if abs(a) < EPS:
        return quad_roots(b, c, d)
    inv = 1.0 / a
    A, B, C = b * inv, c * inv, d * inv
    p = B - A * A / 3.0
    q = A * (2.0 * A * A - 9.0 * B) / 27.0 + C
    off = A / 3.0
    disc = 0.25 * q * q + (p / 3.0) ** 3
    if disc > 1e-14:
        s = math.sqrt(disc)
        roots = [_cbrt(-0.5 * q + s) + _cbrt(-0.5 * q - s) - off]
    elif disc > -1e-14:
        u = _cbrt(-0.5 * q)
        roots = [2.0 * u - off, -u - off]
    else:
        m = 2.0 * math.sqrt(-p / 3.0)
        cos = (3.0 * q / (2.0 * p)) * math.sqrt(-3.0 / p)
        ang = math.acos(max(-1.0, min(1.0, cos))) / 3.0
        roots = [m * math.cos(ang - 2.0 * math.pi * k / 3.0) - off
                 for k in range(3)]
    out = []
    for t in roots:
        for _ in range(2):  # Newton polish on the original polynomial
            f = ((a * t + b) * t + c) * t + d
            fp = (3.0 * a * t + 2.0 * b) * t + c
            if fp == 0.0:
                break
            t -= f / fp
        out.append(t)
    return out


# --------------------------------------------------------------------------
# Outline segments (already in em space)
# --------------------------------------------------------------------------

class Seg(object):
    __slots__ = ("quad", "x0", "y0", "cx", "cy", "x1", "y1",
                 "ax", "ay", "bx", "by", "xmin", "ymin", "xmax", "ymax")


def _axis_extrema(v0, vc, v1):
    den = v0 - 2.0 * vc + v1
    if abs(den) < EPS:
        return min(v0, v1), max(v0, v1)
    t = (v0 - vc) / den
    if 0.0 < t < 1.0:
        v = (den * t + 2.0 * (vc - v0)) * t + v0
        return min(v0, v1, v), max(v0, v1, v)
    return min(v0, v1), max(v0, v1)


def make_line(p0, p1):
    s = Seg()
    s.quad = False
    s.x0, s.y0 = p0
    s.x1, s.y1 = p1
    s.cx = s.cy = s.ax = s.ay = s.bx = s.by = 0.0
    s.xmin, s.xmax = min(s.x0, s.x1), max(s.x0, s.x1)
    s.ymin, s.ymax = min(s.y0, s.y1), max(s.y0, s.y1)
    return s


def make_quad(p0, c, p1):
    s = Seg()
    s.quad = True
    s.x0, s.y0 = p0
    s.cx, s.cy = c
    s.x1, s.y1 = p1
    s.ax = p0[0] - 2.0 * c[0] + p1[0]
    s.ay = p0[1] - 2.0 * c[1] + p1[1]
    s.bx = 2.0 * (c[0] - p0[0])
    s.by = 2.0 * (c[1] - p0[1])
    s.xmin, s.xmax = _axis_extrema(s.x0, s.cx, s.x1)
    s.ymin, s.ymax = _axis_extrema(s.y0, s.cy, s.y1)
    return s


def seg_dist2(s, px, py):
    """Squared distance from point to segment."""
    if not s.quad:
        dx = s.x1 - s.x0
        dy = s.y1 - s.y0
        l2 = dx * dx + dy * dy
        if l2 < EPS:
            ex, ey = s.x0 - px, s.y0 - py
            return ex * ex + ey * ey
        t = ((px - s.x0) * dx + (py - s.y0) * dy) / l2
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        ex = s.x0 + t * dx - px
        ey = s.y0 + t * dy - py
        return ex * ex + ey * ey
    aa = s.ax * s.ax + s.ay * s.ay
    if aa < EPS:  # degenerate: control on the chord -> treat as line
        dx = s.x1 - s.x0
        dy = s.y1 - s.y0
        l2 = dx * dx + dy * dy
        if l2 < EPS:
            ex, ey = s.x0 - px, s.y0 - py
            return ex * ex + ey * ey
        t = ((px - s.x0) * dx + (py - s.y0) * dy) / l2
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        ex = s.x0 + t * dx - px
        ey = s.y0 + t * dy - py
        return ex * ex + ey * ey
    cx = s.x0 - px
    cy = s.y0 - py
    ab = s.ax * s.bx + s.ay * s.by
    bb = s.bx * s.bx + s.by * s.by
    ac = s.ax * cx + s.ay * cy
    bc = s.bx * cx + s.by * cy
    cand = [0.0, 1.0]
    for t in cubic_roots(2.0 * aa, 3.0 * ab, bb + 2.0 * ac, bc):
        if 0.0 < t < 1.0:
            cand.append(t)
    best = None
    for t in cand:
        xt = (s.ax * t + s.bx) * t + s.x0 - px
        yt = (s.ay * t + s.by) * t + s.y0 - py
        d2 = xt * xt + yt * yt
        if best is None or d2 < best:
            best = d2
    return best


def seg_winding(s, px, py):
    """Winding contribution of a +x ray from (px, py); nonzero rule."""
    if not s.quad:
        if (s.y0 <= py) != (s.y1 <= py):
            t = (py - s.y0) / (s.y1 - s.y0)
            if s.x0 + t * (s.x1 - s.x0) > px:
                return 1 if s.y1 > s.y0 else -1
        return 0
    # Split at the y extremum into y-monotonic pieces and apply the
    # lower-inclusive/upper-exclusive convention in *y space*, so vertices
    # shared with neighbours are counted exactly once (no spike artefacts).
    w = 0
    ts = [0.0, 1.0]
    if abs(s.ay) > EPS:
        t_star = -s.by / (2.0 * s.ay)
        if 0.0 < t_star < 1.0:
            ts = [0.0, t_star, 1.0]
    for k in range(len(ts) - 1):
        ta, tb = ts[k], ts[k + 1]
        ya = (s.ay * ta + s.by) * ta + s.y0
        yb = (s.ay * tb + s.by) * tb + s.y0
        if (ya <= py) != (yb <= py):
            for t in quad_roots(s.ay, s.by, s.y0 - py):
                if ta - 1e-9 <= t <= tb + 1e-9:
                    xt = (s.ax * t + s.bx) * t + s.x0
                    if xt > px:
                        w += 1 if yb > ya else -1
                    break
    return w


def signed_distance(px, py, segs):
    """Exact signed distance in em; negative inside (nonzero fill)."""
    best = None
    wind = 0
    for s in segs:
        wind += seg_winding(s, px, py)
        dx = s.xmin - px
        if dx < 0.0:
            dx = px - s.xmax
            if dx < 0.0:
                dx = 0.0
        dy = s.ymin - py
        if dy < 0.0:
            dy = py - s.ymax
            if dy < 0.0:
                dy = 0.0
        if best is not None and dx * dx + dy * dy > best:
            continue  # bbox already farther than current best
        d2 = seg_dist2(s, px, py)
        if best is None or d2 < best:
            best = d2
    if best is None:
        return FAR
    d = math.sqrt(best)
    return -d if wind != 0 else d


# --------------------------------------------------------------------------
# Outline parsing
# --------------------------------------------------------------------------

def _xform(m, x, y):
    return (m[0] * x + m[1] * y + m[4], m[2] * x + m[3] * y + m[5])


def parse_paths(paths, m):
    """Expand one component's paths under its transform into em-space segs."""
    segs = []
    for cmds in paths:
        start = None
        cur = None
        for cmd in cmds:
            op = cmd[0]
            if op == "M":
                if start is not None and cur != start:
                    segs.append(make_line(cur, start))  # implicit close
                cur = start = _xform(m, cmd[1], cmd[2])
            elif op == "L":
                p = _xform(m, cmd[1], cmd[2])
                segs.append(make_line(cur, p))
                cur = p
            elif op == "Q":
                c = _xform(m, cmd[1], cmd[2])
                p = _xform(m, cmd[3], cmd[4])
                segs.append(make_quad(cur, c, p))
                cur = p
            elif op == "Z":
                if start is not None and cur != start:
                    segs.append(make_line(cur, start))
                cur = start
        if start is not None and cur != start:
            segs.append(make_line(cur, start))  # implicit close
    return segs


# --------------------------------------------------------------------------
# Atlas construction
# --------------------------------------------------------------------------

class Block(object):
    __slots__ = ("gid", "segs", "em_x0", "em_y0", "w", "h", "rx", "ry")


def build_atlas(data):
    tpe = float(data.get("texels_per_em", 64))
    padding = float(data.get("padding", 5))

    max_stroke = {}
    for r in data.get("renders", []):
        st = float(r.get("stroke", 0.0) or 0.0)
        g = r["glyph"]
        if st > max_stroke.get(g, 0.0):
            max_stroke[g] = st

    blocks = []
    for g in data.get("glyphs", []):
        segs = []
        for comp in g.get("components", []):
            m = comp.get("transform", [1, 0, 0, 1, 0, 0])
            segs.extend(parse_paths(comp.get("paths", []), m))
        b = Block()
        b.gid = g["id"]
        b.segs = segs
        # margin: requested padding + one texel (AA/bilinear slack) + stroke
        pad_em = (padding + 1.0) / tpe + max_stroke.get(b.gid, 0.0)
        if segs:
            x0 = min(s.xmin for s in segs) - pad_em
            y0 = min(s.ymin for s in segs) - pad_em
            x1 = max(s.xmax for s in segs) + pad_em
            y1 = max(s.ymax for s in segs) + pad_em
        else:
            x0 = y0 = 0.0
            x1 = y1 = 1.0 / tpe
        b.em_x0, b.em_y0 = x0, y0
        b.w = max(1, int(math.ceil((x1 - x0) * tpe - 1e-9)))
        b.h = max(1, int(math.ceil((y1 - y0) * tpe - 1e-9)))
        blocks.append(b)

    # shelf packing, input order; blocks never overlap, gutter between them
    shelf_w = max((b.w for b in blocks), default=0)
    x = y = shelf_h = 0
    atlas_w = 0
    for b in blocks:
        if x > 0 and x + b.w > shelf_w:
            x = 0
            y += shelf_h + GUTTER
            shelf_h = 0
        b.rx, b.ry = x, y
        x += b.w + GUTTER
        if x - GUTTER > atlas_w:
            atlas_w = x - GUTTER
        if b.h > shelf_h:
            shelf_h = b.h
    atlas_h = y + shelf_h

    grid = [FAR] * (atlas_w * atlas_h)
    inv = 1.0 / tpe
    for b in blocks:
        for j in range(b.h):
            py = b.em_y0 + (j + 0.5) * inv
            row = (b.ry + j) * atlas_w + b.rx
            for i in range(b.w):
                px = b.em_x0 + (i + 0.5) * inv
                grid[row + i] = signed_distance(px, py, b.segs)
    return blocks, grid, atlas_w, atlas_h, tpe


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_image(r, block, grid, atlas_w, tpe):
    scale = float(r["scale"])
    ox, oy = float(r["origin"][0]), float(r["origin"][1])
    stroke = float(r.get("stroke", 0.0) or 0.0)
    width, height = int(r["width"]), int(r["height"])
    inv_scale = 1.0 / scale

    x_lo, x_hi = block.rx, block.rx + block.w - 1
    y_lo, y_hi = block.ry, block.ry + block.h - 1

    rows = []
    for j in range(height):
        em_y = ((j + 0.5) - oy) * inv_scale
        gy = (em_y - block.em_y0) * tpe - 0.5 + block.ry
        gy = y_lo if gy < y_lo else (y_hi if gy > y_hi else gy)
        j0 = int(math.floor(gy))
        if j0 >= y_hi:
            j0 = y_hi - 1 if y_hi > y_lo else y_lo
        if j0 < y_lo:
            j0 = y_lo
        j1 = j0 + 1 if j0 + 1 <= y_hi else j0
        fy = gy - j0
        fy = 0.0 if fy < 0.0 else (1.0 if fy > 1.0 else fy)
        row0 = j0 * atlas_w
        row1 = j1 * atlas_w
        out = []
        for i in range(width):
            em_x = ((i + 0.5) - ox) * inv_scale
            gx = (em_x - block.em_x0) * tpe - 0.5 + block.rx
            gx = x_lo if gx < x_lo else (x_hi if gx > x_hi else gx)
            i0 = int(math.floor(gx))
            if i0 >= x_hi:
                i0 = x_hi - 1 if x_hi > x_lo else x_lo
            if i0 < x_lo:
                i0 = x_lo
            i1 = i0 + 1 if i0 + 1 <= x_hi else i0
            fx = gx - i0
            fx = 0.0 if fx < 0.0 else (1.0 if fx > 1.0 else fx)
            d00 = grid[row0 + i0]
            d01 = grid[row0 + i1]
            d10 = grid[row1 + i0]
            d11 = grid[row1 + i1]
            d = (d00 * (1.0 - fx) + d01 * fx) * (1.0 - fy) \
                + (d10 * (1.0 - fx) + d11 * fx) * fy
            # stroke dilates outward; one-pixel-wide linear AA ramp
            c = 0.5 - (d - stroke) * scale
            c = 0.0 if c < 0.0 else (1.0 if c > 1.0 else c)
            out.append(round(c, 6))
        rows.append(out)
    return rows


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def render(data):
    blocks, grid, atlas_w, atlas_h, tpe = build_atlas(data)
    by_id = {b.gid: b for b in blocks}
    glyphs = {}
    for b in blocks:
        glyphs[b.gid] = {
            "atlas_rect": [b.rx, b.ry, b.w, b.h],
            # em coordinate of the centre of texel (rx, ry)
            "em_origin": [round(b.em_x0 + 0.5 / tpe, 9),
                          round(b.em_y0 + 0.5 / tpe, 9)],
        }
    images = []
    for r in data.get("renders", []):
        cov = render_image(r, by_id[r["glyph"]], grid, atlas_w, tpe)
        images.append({"id": r["id"], "coverage": cov})
    return {
        "glyphs": glyphs,
        "atlas": {
            "width": atlas_w,
            "height": atlas_h,
            "texels_per_em": tpe,
            "data": [round(v, 6) for v in grid],
        },
        "images": images,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    out = render(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(out, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
