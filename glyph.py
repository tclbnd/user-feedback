"""Signed-distance-field glyph atlas renderer.

Pipeline
--------
1. Expand every component's quadratic outline through its affine transform
   into em-space segments (lines and quadratic Beziers).  An affine map
   carries a quadratic Bezier to a quadratic Bezier, so transforming the
   control points preserves the curve exactly; mirroring only flips the
   winding, which the nonzero fill rule absorbs.
2. Build one signed-distance-field block per glyph on a texels_per_em grid
   (em units, inside negative, outside positive) with `padding` texels of
   margin, enlarged so that the largest requested stroke dilation is still
   covered.  The sign comes from the nonzero winding number; the magnitude
   is the exact distance to the nearest segment (cubic root solve for
   quadratic Beziers).
3. Shelf-pack the blocks into a single non-overlapping atlas.  Each glyph
   keeps a single block that is reused for every render size.
4. Render coverage by mapping pixel centres back to em space, sampling the
   glyph's own atlas block with clamped bilinear interpolation (never
   reading a neighbouring block), dilating by `stroke`, and applying a
   one-screen-pixel antialiasing ramp so the transition width is constant
   at every scale and sub-pixel translation.
"""
import argparse
import json
import math
from pathlib import Path

DEFAULT_TEXELS_PER_EM = 64
DEFAULT_PADDING = 5
EMPTY_TEXEL = 1.0e6  # filler for atlas area outside every block


# ------------------------------------------------------------------ curves

def _xf(t, x, y):
    a, b, c, d, tx, ty = t
    return (a * x + b * y + tx, c * x + d * y + ty)


def glyph_segments(glyph):
    """Flatten all components into em-space segments.

    Returns a list of ("L", p0, p1) and ("Q", p0, p1, p2) tuples with
    implicit closure of every (sub)contour and degenerate pieces dropped.
    """
    segs = []
    for comp in glyph.get("components", []):
        t = comp.get("transform", [1, 0, 0, 1, 0, 0])
        for path in comp.get("paths", []):
            start = None
            pos = None
            for cmd in path:
                op = cmd[0]
                if op == "M":
                    if pos is not None and start is not None and pos != start:
                        segs.append(("L", pos, start))
                    pos = _xf(t, cmd[1], cmd[2])
                    start = pos
                elif op == "L":
                    p = _xf(t, cmd[1], cmd[2])
                    if p != pos:
                        segs.append(("L", pos, p))
                    pos = p
                elif op == "Q":
                    c = _xf(t, cmd[1], cmd[2])
                    p = _xf(t, cmd[3], cmd[4])
                    if not (pos == c == p):
                        segs.append(("Q", pos, c, p))
                    pos = p
                elif op == "Z":
                    if pos is not None and start is not None and pos != start:
                        segs.append(("L", pos, start))
                    pos = start
            if pos is not None and start is not None and pos != start:
                segs.append(("L", pos, start))
    return segs


def seg_bbox(seg):
    """Tight axis-aligned bounding box of one segment."""
    if seg[0] == "L":
        (x0, y0), (x1, y1) = seg[1], seg[2]
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    _, p0, p1, p2 = seg
    xs = [p0[0], p2[0]]
    ys = [p0[1], p2[1]]
    for (a, b, c), acc in (((p0[0], p1[0], p2[0]), xs),
                           ((p0[1], p1[1], p2[1]), ys)):
        den = a - 2.0 * b + c
        if den != 0.0:
            t = (a - b) / den
            if 0.0 < t < 1.0:
                mt = 1.0 - t
                acc.append(mt * mt * a + 2.0 * mt * t * b + t * t * c)
    return min(xs), min(ys), max(xs), max(ys)


def segments_bbox(segs):
    boxes = [seg_bbox(s) for s in segs]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


# ------------------------------------------------------- distance to outline

def _roots01(a, b, c, d):
    """Real roots of a*t^3 + b*t^2 + c*t + d inside the open interval (0, 1).

    Splits [0, 1] at the critical points of the polynomial and bisects each
    monotonic piece, so it degrades gracefully for lower-degree inputs.
    """
    def f(t):
        return ((a * t + b) * t + c) * t + d

    pts = [0.0, 1.0]
    if a != 0.0:
        disc = 4.0 * b * b - 12.0 * a * c
        if disc > 0.0:
            s = math.sqrt(disc)
            for t in ((-2.0 * b - s) / (6.0 * a), (-2.0 * b + s) / (6.0 * a)):
                if 0.0 < t < 1.0:
                    pts.append(t)
    elif b != 0.0:
        t = -c / (2.0 * b)
        if 0.0 < t < 1.0:
            pts.append(t)
    pts.sort()
    roots = []
    for i in range(len(pts) - 1):
        lo, hi = pts[i], pts[i + 1]
        flo, fhi = f(lo), f(hi)
        if flo == 0.0:
            if lo != 0.0:
                roots.append(lo)
            continue
        if fhi == 0.0:
            if hi != 1.0:
                roots.append(hi)
            continue
        if flo * fhi > 0.0:
            continue
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if flo * f(mid) <= 0.0:
                hi = mid
            else:
                lo, flo = mid, f(lo)
        roots.append(0.5 * (lo + hi))
    return roots


def dist2_point_seg(px, py, seg):
    """Squared distance from (px, py) to a line or quadratic Bezier segment."""
    if seg[0] == "L":
        _, (x0, y0), (x1, y1) = seg
        vx, vy = x1 - x0, y1 - y0
        wx, wy = px - x0, py - y0
        l2 = vx * vx + vy * vy
        t = 0.0 if l2 == 0.0 else max(0.0, min(1.0, (wx * vx + wy * vy) / l2))
        dx, dy = wx - t * vx, wy - t * vy
        return dx * dx + dy * dy
    _, p0, p1, p2 = seg
    # B(t) - p = a t^2 + b t + c with
    ax = p0[0] - 2.0 * p1[0] + p2[0]
    ay = p0[1] - 2.0 * p1[1] + p2[1]
    bx = 2.0 * (p1[0] - p0[0])
    by = 2.0 * (p1[1] - p0[1])
    cx = p0[0] - px
    cy = p0[1] - py
    # d/dt |B-p|^2 == 2 * (a t^2 + b t + c) . (2a t + b): a cubic.
    ca = 2.0 * (ax * ax + ay * ay)
    cb = 3.0 * (ax * bx + ay * by)
    cc = 2.0 * (ax * cx + ay * cy) + bx * bx + by * by
    cd = bx * cx + by * cy
    best = cx * cx + cy * cy                      # t = 0
    ex, ey = ax + bx + cx, ay + by + cy           # t = 1
    e2 = ex * ex + ey * ey
    if e2 < best:
        best = e2
    for t in _roots01(ca, cb, cc, cd):
        qx = (ax * t + bx) * t + cx
        qy = (ay * t + by) * t + cy
        d2 = qx * qx + qy * qy
        if d2 < best:
            best = d2
    return best


# ------------------------------------------------------- winding (nonzero)

def _quad_roots01(a, b, c):
    """Roots of a*t^2 + b*t + c in [0, 1]; tangential touches return []."""
    if a == 0.0:
        if b == 0.0:
            return []
        t = -c / b
        return [t] if 0.0 <= t <= 1.0 else []
    disc = b * b - 4.0 * a * c
    if disc <= 0.0:
        # No crossing, or a tangential touch: either way the signed crossing
        # count is zero.
        return []
    s = math.sqrt(disc)
    out = []
    for t in ((-b - s) / (2.0 * a), (-b + s) / (2.0 * a)):
        if 0.0 <= t <= 1.0:
            out.append(t)
    return out


def _winding_once(px, py, segs):
    """Nonzero winding number via a +x ray; flags degenerate configurations."""
    wn = 0
    degen = False
    for seg in segs:
        if seg[0] == "L":
            _, (x0, y0), (x1, y1) = seg
            if (y0 > py) != (y1 > py):
                t = (py - y0) / (y1 - y0)
                x = x0 + t * (x1 - x0)
                if x > px:
                    wn += 1 if y1 > y0 else -1
                elif x == px:
                    degen = True
        else:
            _, (x0, y0), (x1, y1), (x2, y2) = seg
            ay = y0 - 2.0 * y1 + y2
            by = 2.0 * (y1 - y0)
            cy = y0 - py
            for t in _quad_roots01(ay, by, cy):
                if t <= 1e-12 or t >= 1.0 - 1e-12:
                    # A shared endpoint sits exactly on the ray: the crossing
                    # cannot be classified without the neighbour, so perturb.
                    degen = True
                    continue
                dydt = 2.0 * ay * t + by
                if dydt == 0.0:
                    degen = True
                    continue
                mt = 1.0 - t
                x = mt * mt * x0 + 2.0 * mt * t * x1 + t * t * x2
                if x > px:
                    wn += 1 if dydt > 0.0 else -1
                elif x == px:
                    degen = True
    return wn, degen


_PERTURB = ((1.31e-7, 2.71e-7), (3.73e-7, 1.91e-7), (5.29e-7, 7.13e-7))


def winding_number(px, py, segs):
    """Nonzero winding number; retries with a tiny offset on degeneracies.

    A perturbation of ~1e-7 em can only flip the sign of points that lie
    within 1e-7 em of the outline, where the coverage is 0.5 either way.
    """
    wn = 0
    for k in range(len(_PERTURB) + 1):
        dx, dy = (0.0, 0.0) if k == 0 else _PERTURB[k - 1]
        wn, degen = _winding_once(px + dx, py + dy, segs)
        if not degen:
            break
    return wn


# ------------------------------------------------------------ atlas building

def _sdf_block(segs, w, h, em_origin, tpe):
    """Signed distance (em, inside negative) sampled at texel centres."""
    ox, oy = em_origin
    data = [0.0] * (w * h)
    for j in range(h):
        py = oy + (j + 0.5) / tpe
        row = j * w
        for i in range(w):
            px = ox + (i + 0.5) / tpe
            d2 = None
            for s in segs:
                v = dist2_point_seg(px, py, s)
                if d2 is None or v < d2:
                    d2 = v
            d = math.sqrt(d2)
            if winding_number(px, py, segs) != 0:
                d = -d
            data[row + i] = d
    return data


def build_context(data):
    """Build the atlas: per-glyph SDF blocks packed without overlap."""
    glyphs = data.get("glyphs", [])
    renders = data.get("renders", [])
    tpe = data.get("texels_per_em", DEFAULT_TEXELS_PER_EM)
    padding = data.get("padding", DEFAULT_PADDING)

    max_stroke = {}
    for g in glyphs:
        max_stroke[g["id"]] = 0.0
    for r in renders:
        gid = r.get("glyph")
        stroke = r.get("stroke", 0) or 0
        if gid in max_stroke and stroke > max_stroke[gid]:
            max_stroke[gid] = stroke

    entries = {}
    order = []
    for g in glyphs:
        gid = g["id"]
        segs = glyph_segments(g)
        pad_tex = padding + int(math.ceil(max_stroke[gid] * tpe))
        if not segs:
            entries[gid] = {"rect": (0, 0, 0, 0), "em_origin": (0.0, 0.0),
                            "w": 0, "h": 0, "data": []}
            order.append(gid)
            continue
        x0, y0, x1, y1 = segments_bbox(segs)
        w = int(math.ceil((x1 - x0) * tpe - 1e-9)) + 2 * pad_tex
        h = int(math.ceil((y1 - y0) * tpe - 1e-9)) + 2 * pad_tex
        em_origin = (x0 - pad_tex / tpe, y0 - pad_tex / tpe)
        block = _sdf_block(segs, w, h, em_origin, tpe)
        entries[gid] = {"rect": None, "em_origin": em_origin,
                        "w": w, "h": h, "data": block}
        order.append(gid)

    # Shelf packing, deterministic for a given glyph order.
    total = sum(e["w"] * e["h"] for e in entries.values())
    maxw = max((e["w"] for e in entries.values()), default=0)
    aw = max(maxw, int(math.sqrt(total)) + 1, 1)
    x = y = shelf_h = 0
    for gid in order:
        e = entries[gid]
        w, h = e["w"], e["h"]
        if w == 0 or h == 0:
            e["rect"] = (0, 0, 0, 0)
            continue
        if x + w > aw:
            x = 0
            y += shelf_h
            shelf_h = 0
        e["rect"] = (x, y, w, h)
        x += w
        shelf_h = max(shelf_h, h)
    ah = y + shelf_h

    atlas = [EMPTY_TEXEL] * (aw * ah)
    for gid in order:
        e = entries[gid]
        rx, ry, w, h = e["rect"]
        for j in range(h):
            dst = (ry + j) * aw + rx
            atlas[dst:dst + w] = e["data"][j * w:(j + 1) * w]

    # Round the published grid once; rendering below uses exactly these
    # values so the atlas and the images stay consistent.  12 decimals keep
    # exact edge-on-pixel-centre ties exact after the coverage rounding.
    atlas = [round(v, 12) for v in atlas]

    return {"tpe": tpe, "padding": padding, "entries": entries,
            "order": order, "atlas": atlas, "aw": aw, "ah": ah}


# ---------------------------------------------------------------- rendering

def _sample_block(ctx, gid, u, v):
    """Clamped bilinear sample; texel (u, v) is in block-local coordinates.

    Coordinates are clamped to the glyph's own atlas rectangle, so a sample
    can never read a neighbouring block regardless of scale or offset.
    """
    e = ctx["entries"][gid]
    rx, ry, rw, rh = e["rect"]
    if rw == 0 or rh == 0:
        return 0.0
    u = 0.0 if u < 0.0 else (rw - 1.0 if u > rw - 1.0 else u)
    v = 0.0 if v < 0.0 else (rh - 1.0 if v > rh - 1.0 else v)
    i0 = int(math.floor(u))
    j0 = int(math.floor(v))
    i1 = i0 + 1 if i0 + 1 < rw else rw - 1
    j1 = j0 + 1 if j0 + 1 < rh else rh - 1
    fu = u - i0
    fv = v - j0
    atlas, aw = ctx["atlas"], ctx["aw"]
    a = atlas[(ry + j0) * aw + rx + i0]
    b = atlas[(ry + j0) * aw + rx + i1]
    c = atlas[(ry + j1) * aw + rx + i0]
    d = atlas[(ry + j1) * aw + rx + i1]
    return (a * (1.0 - fu) + b * fu) * (1.0 - fv) + (c * (1.0 - fu) + d * fu) * fv


def render_images(data, ctx):
    """Coverage for every render, in input order, via a one-pixel ramp."""
    tpe = ctx["tpe"]
    images = []
    for r in data.get("renders", []):
        gid = r["glyph"]
        width, height = r["width"], r["height"]
        scale = r["scale"]
        ox, oy = r.get("origin", [0.0, 0.0])
        stroke = r.get("stroke", 0) or 0
        e = ctx["entries"].get(gid)
        cov = [[0.0] * width for _ in range(height)]
        if e is not None and e["w"] > 0:
            eox, eoy = e["em_origin"]
            for j in range(height):
                ey = (j + 0.5 - oy) / scale
                v = (ey - eoy) * tpe - 0.5
                row = cov[j]
                for i in range(width):
                    ex = (i + 0.5 - ox) / scale
                    u = (ex - eox) * tpe - 0.5
                    d = (_sample_block(ctx, gid, u, v) - stroke) * scale
                    c = 0.5 - d
                    row[i] = round(0.0 if c < 0.0 else (1.0 if c > 1.0 else c), 6)
        images.append({"id": r["id"], "coverage": cov})
    return images


def render(data):
    ctx = build_context(data)
    glyphs_out = {}
    for gid in ctx["order"]:
        e = ctx["entries"][gid]
        glyphs_out[gid] = {
            "atlas_rect": [round(v, 12) if isinstance(v, float) else v
                           for v in e["rect"]],
            "em_origin": [round(e["em_origin"][0], 12),
                          round(e["em_origin"][1], 12)],
        }
    return {
        "glyphs": glyphs_out,
        "atlas": {"width": ctx["aw"], "height": ctx["ah"],
                  "texels_per_em": ctx["tpe"], "padding": ctx["padding"],
                  "data": ctx["atlas"]},
        "images": render_images(data, ctx),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),
                                   sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
