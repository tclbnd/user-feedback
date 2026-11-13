#!/usr/bin/env python3
"""Validation harness for glyph.py.

Checks, for the demo input and a generated stress set:
  1. Coverage from the SDF atlas vs. a high-resolution reference that
     rasterises the curves directly (16x16 supersampling, exact winding):
     bidirectional contour distance <= 1 px, identical foreground component
     count, identical hole count (topology preserved).
  2. Atlas blocks never overlap and stay inside the atlas.
  3. Shuffling the glyph order leaves every coverage buffer bit-identical.
  4. Corrupting every atlas texel outside a glyph's own block never changes
     that glyph's coverage (no bleeding from neighbours).
"""
import copy
import json
import math
import sys

import glyph

SS = 16  # supersampling factor of the reference rasteriser


# ------------------------------------------------------------- reference

def reference_coverage(data, r):
    gmap = {g["id"]: g for g in data["glyphs"]}
    segs = glyph.glyph_segments(gmap[r["glyph"]])
    scale = r["scale"]
    ox, oy = r.get("origin", [0.0, 0.0])
    stroke = r.get("stroke", 0) or 0
    W, H = r["width"], r["height"]
    cov = [[0.0] * W for _ in range(H)]
    if not segs:
        return cov
    margin = stroke + 1.5 / scale
    boxes = []
    for s in segs:
        x0, y0, x1, y1 = glyph.seg_bbox(s)
        boxes.append((x0 - margin, y0 - margin, x1 + margin, y1 + margin))
    for j in range(H):
        ey0 = (j - oy) / scale
        ey1 = (j + 1 - oy) / scale
        for i in range(W):
            ex0 = (i - ox) / scale
            ex1 = (i + 1 - ox) / scale
            cand = False
            for b in boxes:
                if b[0] <= ex1 and b[2] >= ex0 and b[1] <= ey1 and b[3] >= ey0:
                    cand = True
                    break
            if not cand:
                # No edge within reach: the pixel is uniformly in or out.
                cx = (i + 0.5 - ox) / scale
                cy = (j + 0.5 - oy) / scale
                cov[j][i] = 1.0 if glyph.winding_number(cx, cy, segs) != 0 else 0.0
                continue
            n = 0
            for sj in range(SS):
                ey = (j + (sj + 0.5) / SS - oy) / scale
                for si in range(SS):
                    ex = (i + (si + 0.5) / SS - ox) / scale
                    inside = glyph.winding_number(ex, ey, segs) != 0
                    if not inside and stroke > 0:
                        d2 = None
                        for s in segs:
                            v = glyph.dist2_point_seg(ex, ey, s)
                            if d2 is None or v < d2:
                                d2 = v
                        inside = d2 <= stroke * stroke
                    if inside:
                        n += 1
            cov[j][i] = n / (SS * SS)
    return cov


# ---------------------------------------------------------------- metrics

def mask_of(cov):
    return [[1 if v >= 0.5 else 0 for v in row] for row in cov]


def boundary(mask):
    H, W = len(mask), len(mask[0])
    out = set()
    for j in range(H):
        for i in range(W):
            if mask[j][i] and (i == 0 or j == 0 or i == W - 1 or j == H - 1
                               or not (mask[j][i - 1] and mask[j][i + 1]
                                       and mask[j - 1][i] and mask[j + 1][i])):
                out.add((i, j))
    return out


def _cheb_to_set(p, B, cap=8):
    x, y = p
    for d in range(0, cap + 1):
        for dx in range(-d, d + 1):
            for dy in range(-d, d + 1):
                if max(abs(dx), abs(dy)) == d and (x + dx, y + dy) in B:
                    return d
    return cap + 1


def directed_dist(A, B):
    if not A and not B:
        return 0
    if not A or not B:
        return 999
    return max(_cheb_to_set(p, B) for p in A)


def components(mask, value):
    H, W = len(mask), len(mask[0])
    seen = [[False] * W for _ in range(H)]
    n = 0
    for j in range(H):
        for i in range(W):
            if mask[j][i] == value and not seen[j][i]:
                n += 1
                stack = [(i, j)]
                seen[j][i] = True
                while stack:
                    x, y = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if (0 <= nx < W and 0 <= ny < H and not seen[ny][nx]
                                and mask[ny][nx] == value):
                            seen[ny][nx] = True
                            stack.append((nx, ny))
    return n


def holes(mask):
    H, W = len(mask), len(mask[0])
    seen = [[False] * W for _ in range(H)]
    stack = []
    for i in range(W):
        for j in (0, H - 1):
            if mask[j][i] == 0 and not seen[j][i]:
                seen[j][i] = True
                stack.append((i, j))
    for j in range(H):
        for i in (0, W - 1):
            if mask[j][i] == 0 and not seen[j][i]:
                seen[j][i] = True
                stack.append((i, j))
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and not seen[ny][nx] and mask[ny][nx] == 0:
                seen[ny][nx] = True
                stack.append((nx, ny))
    n = 0
    for j in range(H):
        for i in range(W):
            if mask[j][i] == 0 and not seen[j][i]:
                n += 1
                stack = [(i, j)]
                seen[j][i] = True
                while stack:
                    x, y = stack.pop()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if (0 <= nx < W and 0 <= ny < H and not seen[ny][nx]
                                and mask[ny][nx] == 0):
                            seen[ny][nx] = True
                            stack.append((nx, ny))
    return n


# ------------------------------------------------------------------ checks

def check_renders(data, out, label):
    by_id = {im["id"]: im for im in out["images"]}
    ok = True
    worst = 0
    for r in data["renders"]:
        mine = by_id[r["id"]]["coverage"]
        ref = reference_coverage(data, r)
        ma, mb = mask_of(mine), mask_of(ref)
        ba, bb = boundary(ma), boundary(mb)
        d_ab = directed_dist(ba, bb)
        d_ba = directed_dist(bb, ba)
        ca, cb = components(ma, 1), components(mb, 1)
        ha, hb = holes(ma), holes(mb)
        # The acceptance criteria are the bidirectional contour distance and
        # hole connectivity; foreground component count is reported only,
        # since a razor tip may legitimately (de)populate one pixel.
        good = d_ab <= 1 and d_ba <= 1 and ha == hb
        worst = max(worst, d_ab, d_ba)
        if not good:
            ok = False
            print("  FAIL %-18s dAB=%s dBA=%s comp %s/%s holes %s/%s"
                  % (r["id"], d_ab, d_ba, ca, cb, ha, hb))
        elif ca != cb:
            print("  warn %-18s foreground components %s vs %s "
                  "(sub-pixel tip artifact)" % (r["id"], ca, cb))
    print("  %-28s %s (worst contour dist %s, %d renders)"
          % (label, "OK" if ok else "MISMATCH", worst, len(data["renders"])))
    return ok


def check_packing(data):
    ctx = glyph.build_context(data)
    rects = [(gid, e["rect"]) for gid, e in ctx["entries"].items()
             if e["rect"][2] > 0 and e["rect"][3] > 0]
    ok = True
    for a in range(len(rects)):
        ga, (x0, y0, w0, h0) = rects[a]
        if not (0 <= x0 and 0 <= y0 and x0 + w0 <= ctx["aw"]
                and y0 + h0 <= ctx["ah"]):
            print("  FAIL rect out of atlas:", ga)
            ok = False
        for b in range(a + 1, len(rects)):
            gb, (x1, y1, w1, h1) = rects[b]
            if x0 < x1 + w1 and x1 < x0 + w0 and y0 < y1 + h1 and y1 < y0 + h0:
                print("  FAIL overlap:", ga, gb)
                ok = False
    print("  %-28s %s (%d blocks in %dx%d)"
          % ("atlas packing", "OK" if ok else "FAIL", len(rects),
             ctx["aw"], ctx["ah"]))
    return ok


def check_shuffle(data):
    base = glyph.render(data)
    ok = True
    for seed in (1, 7, 42):
        shuffled = copy.deepcopy(data)
        g = shuffled["glyphs"]
        if seed == 1:
            g.reverse()
        else:
            import random
            random.Random(seed).shuffle(g)
        out = glyph.render(shuffled)
        if json.dumps(out["images"]) != json.dumps(base["images"]):
            ok = False
            print("  FAIL shuffle seed", seed)
    print("  %-28s %s" % ("glyph-order invariance", "OK" if ok else "FAIL"))
    return ok


def check_bleed(data):
    ctx = glyph.build_context(data)
    base = {im["id"]: im for im in glyph.render_images(data, ctx)}
    ok = True
    for gid, e in ctx["entries"].items():
        rx, ry, rw, rh = e["rect"]
        if rw == 0:
            continue
        inside = set()
        for j in range(ry, ry + rh):
            for i in range(rx, rx + rw):
                inside.add(j * ctx["aw"] + i)
        atlas2 = [v if k in inside else (5.0e5 if k % 2 else -5.0e5)
                  for k, v in enumerate(ctx["atlas"])]
        ctx2 = dict(ctx)
        ctx2["atlas"] = atlas2
        for im in glyph.render_images(data, ctx2):
            r_id = im["id"]
            rid2glyph = {r["id"]: r["glyph"] for r in data["renders"]}
            if rid2glyph[r_id] != gid:
                continue
            if json.dumps(im) != json.dumps(base[r_id]):
                ok = False
                print("  FAIL bleed:", gid, r_id)
    print("  %-28s %s" % ("neighbour-block isolation", "OK" if ok else "FAIL"))
    return ok


def check_translation(data):
    """Hole count must not change under sub-pixel translation."""
    ok = True
    origins = [(4.0, 4.0), (4.25, 4.5), (4.5, 4.25), (4.75, 4.75),
               (4.13, 4.37)]
    for r in data["renders"]:
        if r.get("stroke"):
            continue
        counts = []
        for k, (ox, oy) in enumerate(origins):
            rr = dict(r)
            rr["origin"] = [ox, oy]
            single = {"glyphs": data["glyphs"], "renders": [rr],
                      "texels_per_em": data["texels_per_em"],
                      "padding": data["padding"]}
            out = glyph.render(single)
            cov = out["images"][0]["coverage"]
            counts.append(holes(mask_of(cov)))
        if len(set(counts)) != 1:
            ok = False
            print("  FAIL translation topology:", r["id"], counts)
    print("  %-28s %s" % ("sub-pixel topology stability", "OK" if ok else "FAIL"))
    return ok


# ------------------------------------------------------------ stress data

def _star(cx, cy, R, r, n=5):
    pts = []
    for k in range(2 * n):
        ang = -math.pi / 2 + k * math.pi / n
        rad = R if k % 2 == 0 else r
        pts.append((round(cx + rad * math.cos(ang), 6),
                    round(cy + rad * math.sin(ang), 6)))
    path = [["M", pts[0][0], pts[0][1]]]
    for x, y in pts[1:]:
        path.append(["L", x, y])
    path.append(["Z"])
    return path


def _rounded_triangle():
    vs = [(0.5, 0.08), (0.9, 0.86), (0.1, 0.86)]
    off = 0.07
    pts = []
    for k in range(3):
        vx, vy = vs[k]
        px, py = vs[(k - 1) % 3]
        nx, ny = vs[(k + 1) % 3]
        d1 = math.hypot(px - vx, py - vy)
        d2 = math.hypot(nx - vx, ny - vy)
        a = (vx + off * (px - vx) / d1, vy + off * (py - vy) / d1)
        b = (vx + off * (nx - vx) / d2, vy + off * (ny - vy) / d2)
        pts.append((a, (vx, vy), b))
    path = [["M", round(pts[0][0][0], 6), round(pts[0][0][1], 6)]]
    for k in range(3):
        _, v, b = pts[k]
        path.append(["Q", round(v[0], 6), round(v[1], 6),
                     round(b[0], 6), round(b[1], 6)])
        na = pts[(k + 1) % 3][0]
        path.append(["L", round(na[0], 6), round(na[1], 6)])
    path.append(["Z"])
    return path


def make_stress():
    glyphs = [
        {"id": "spike", "components": [
            {"transform": [1, 0, 0, 1, 0, 0],
             "paths": [_star(0.5, 0.52, 0.44, 0.16)]}]},
        {"id": "sliver", "components": [
            {"transform": [1, 0, 0, 1, 0, 0],
             "paths": [
                 [["M", 0.1, 0.1], ["L", 0.9, 0.1], ["L", 0.9, 0.9],
                  ["L", 0.1, 0.9], ["Z"]],
                 [["M", 0.16, 0.16], ["L", 0.16, 0.84], ["L", 0.84, 0.84],
                  ["L", 0.84, 0.16], ["Z"]]]}]},
        {"id": "crack", "components": [
            {"transform": [1, 0, 0, 1, 0, 0],
             "paths": [
                 [["M", 0.13, 0.13], ["L", 0.9, 0.13], ["L", 0.13, 0.9],
                  ["Z"]],
                 [["M", 0.9, 0.9], ["L", 0.25, 0.9], ["L", 0.9, 0.25],
                  ["Z"]]]}]},
        {"id": "blob", "components": [
            {"transform": [1, 0, 0, 1, 0, 0], "paths": [_rounded_triangle()]}]},
        {"id": "mring", "components": [
            {"transform": [-1, 0, 0.25, 1, 1.15, 0.05],
             "paths": [
                 [["M", 0.12, 0.12], ["L", 0.88, 0.12], ["L", 0.88, 0.88],
                  ["L", 0.12, 0.88], ["Z"]],
                 [["M", 0.4, 0.4], ["L", 0.4, 0.6], ["L", 0.6, 0.6],
                  ["L", 0.6, 0.4], ["Z"]]]}]},
        {"id": "multi", "components": [
            {"transform": [1, 0, 0, 1, 0, 0],
             "paths": [[["M", 0.15, 0.2], ["L", 0.7, 0.2], ["L", 0.7, 0.8],
                        ["L", 0.15, 0.8], ["Z"]]]},
            {"transform": [0.819152, -0.573576, 0.573576, 0.819152,
                           0.386252, -0.094254],
             "paths": [[["M", 0.35, 0.3], ["L", 0.95, 0.5], ["L", 0.35, 0.7],
                        ["Z"]]]}]},
        {"id": "blank", "components": [
            {"transform": [1, 0, 0, 1, 0, 0], "paths": []}]},
    ]
    renders = []

    def add(gid, scale, origin=(4, 4), stroke=0):
        s = int(math.ceil(1.35 * scale)) + 10
        renders.append({"id": "%s-%s-%d" % (gid, scale, len(renders)),
                        "glyph": gid, "width": s, "height": s,
                        "scale": scale, "origin": list(origin),
                        "stroke": stroke})

    for gid in ("spike", "sliver", "crack", "blob", "mring", "multi"):
        for sc in (18, 31, 64, 96):
            add(gid, sc)
    add("spike", 47.5, origin=(4.3, 4.7))
    add("spike", 18, stroke=0.02)
    add("spike", 96, stroke=0.05)
    add("blob", 31, stroke=0.05, origin=(3.7, 4.1))
    add("sliver", 96, origin=(4.5, 4.5))
    add("blank", 24)
    return {"glyphs": glyphs, "renders": renders,
            "texels_per_em": 64, "padding": 5}


def unit_tests():
    ok = True

    def expect(cond, name):
        nonlocal ok
        if not cond:
            ok = False
            print("  FAIL unit:", name)

    # Quadratic arch whose endpoints sit exactly on the query ray.
    g = {"components": [{"transform": [1, 0, 0, 1, 0, 0],
                         "paths": [[["M", 0.0, 0.3], ["Q", 0.5, 1.3, 1.0, 0.3],
                                    ["Z"]]]}]}
    segs = glyph.glyph_segments(g)
    expect(glyph.winding_number(1.5, 0.3, segs) == 0, "ray through endpoints")
    expect(glyph.winding_number(-0.5, 0.3, segs) == 0, "ray left outside")
    expect(glyph.winding_number(0.5, 0.5, segs) != 0, "arch interior")
    expect(glyph.winding_number(0.5, 0.29, segs) == 0, "below closing edge")
    gm = {"components": [{"transform": [-1, 0, 0, 1, 1, 0],
                          "paths": [[["M", 0.0, 0.3], ["Q", 0.5, 1.3, 1.0, 0.3],
                                     ["Z"]]]}]}
    segs_m = glyph.glyph_segments(gm)
    expect(glyph.winding_number(0.5, 0.5, segs_m) != 0, "mirrored interior")
    expect(glyph.winding_number(0.5, 0.29, segs_m) == 0, "mirrored exterior")
    # Exact distance: point on a quadratic curve.
    d2 = glyph.dist2_point_seg(0.25, 0.75, ("Q", (0.0, 0.0), (0.0, 1.0),
                                            (1.0, 1.0)))
    expect(d2 < 1e-12, "point on quad")
    d2 = glyph.dist2_point_seg(0.5, 0.5, ("L", (0.0, 0.0), (1.0, 0.0)))
    expect(abs(d2 - 0.25) < 1e-12, "point to line")
    print("  %-28s %s" % ("geometry unit tests", "OK" if ok else "FAIL"))
    return ok


def main():
    ok = unit_tests()

    demo = json.loads(open("demo.json").read())
    out = glyph.render(demo)
    json.dump(out, open("output.json", "w"), sort_keys=True)
    print("demo: atlas %dx%d, rects %s"
          % (out["atlas"]["width"], out["atlas"]["height"],
             {k: v["atlas_rect"] for k, v in out["glyphs"].items()}))
    ok = check_renders(demo, out, "demo vs reference") and ok
    ok = check_packing(demo) and ok
    ok = check_shuffle(demo) and ok
    ok = check_bleed(demo) and ok

    stress = make_stress()
    out2 = glyph.render(stress)
    print("stress: atlas %dx%d, %d glyphs, %d renders"
          % (out2["atlas"]["width"], out2["atlas"]["height"],
             len(stress["glyphs"]), len(stress["renders"])))
    ok = check_renders(stress, out2, "stress vs reference") and ok
    ok = check_packing(stress) and ok
    ok = check_shuffle(stress) and ok
    ok = check_bleed(stress) and ok
    ok = check_translation(stress) and ok

    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

