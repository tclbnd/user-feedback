"""Acceptance checks for glyph.py against a direct-curve reference."""
import json
import math
import sys
import glyph


def glyph_segs(data, gid):
    g = [g for g in data["glyphs"] if g["id"] == gid][0]
    segs = []
    for comp in g["components"]:
        segs.extend(glyph.parse_paths(comp["paths"], comp["transform"]))
    return segs


def ref_coverage(segs, r, S=8):
    """High-res reference: supersampled direct signed-distance tests."""
    scale = r["scale"]
    ox, oy = r["origin"]
    stroke = r.get("stroke", 0.0) or 0.0
    W, H = r["width"], r["height"]
    rows = []
    for j in range(H):
        row = []
        for i in range(W):
            n = 0
            for sj in range(S):
                y = (j + (sj + 0.5) / S - oy) / scale
                for si in range(S):
                    x = (i + (si + 0.5) / S - ox) / scale
                    if glyph.signed_distance(x, y, segs) - stroke < 0:
                        n += 1
            row.append(n / (S * S))
        rows.append(row)
    return rows


def ref_distance(segs, r):
    """Exact signed distance (minus stroke) at every pixel centre."""
    scale = r["scale"]
    ox, oy = r["origin"]
    stroke = r.get("stroke", 0.0) or 0.0
    W, H = r["width"], r["height"]
    return [[glyph.signed_distance((i + 0.5 - ox) / scale,
                                   (j + 0.5 - oy) / scale, segs) - stroke
             for i in range(W)] for j in range(H)]


def contour_from_sign(val):
    """Contour pixels: 4-neighbourhoods straddling zero."""
    W, H = len(val[0]), len(val)
    pts = set()
    for j in range(H):
        for i in range(W):
            inside = val[j][i] < 0.0
            for dj, di in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                jj, ii = j + dj, i + di
                if 0 <= jj < H and 0 <= ii < W:
                    if (val[jj][ii] < 0.0) != inside:
                        pts.add((i, j))
    return pts


def contour_pixels(cov):
    W, H = len(cov[0]), len(cov)
    pts = set()
    for j in range(H):
        for i in range(W):
            c = cov[j][i] - 0.5
            for dj, di in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                jj, ii = j + dj, i + di
                if 0 <= jj < H and 0 <= ii < W:
                    if (cov[jj][ii] - 0.5) * c < 0:
                        pts.add((i, j))
    return pts


def chamfer(A, B, trace=False):
    def nearest(p, S):
        x, y = p
        best = 1e18
        bp = None
        for (u, v) in S:
            d = (x - u) ** 2 + (y - v) ** 2
            if d < best:
                best = d
                bp = (u, v)
        return math.sqrt(best), bp
    if not A or not B:
        return (0.0 if A == B else 1e18)
    d1, p1, q1 = 0.0, None, None
    for p in A:
        d, q = nearest(p, B)
        if d > d1:
            d1, p1, q1 = d, p, q
    d2, p2, q2 = 0.0, None, None
    for p in B:
        d, q = nearest(p, A)
        if d > d2:
            d2, p2, q2 = d, p, q
    if trace:
        print("    worst A->B: %s -> %s (%.3f); worst B->A: %s -> %s (%.3f)"
              % (p1, q1, d1, q2, p2, d2))
    return max(d1, d2)


def hole_components(cov):
    """Components of cov < 0.5 that do not touch the image border."""
    W, H = len(cov[0]), len(cov)
    seen = [[False] * W for _ in range(H)]
    count = 0
    for j in range(H):
        for i in range(W):
            if cov[j][i] < 0.5 and not seen[j][i]:
                stack = [(i, j)]
                seen[j][i] = True
                touches = False
                cells = 0
                while stack:
                    x, y = stack.pop()
                    cells += 1
                    if x in (0, W - 1) or y in (0, H - 1):
                        touches = True
                    for dj, di in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                        jj, ii = y + dj, x + di
                        if (0 <= jj < H and 0 <= ii < W
                                and cov[jj][ii] < 0.5 and not seen[jj][ii]):
                            seen[jj][ii] = True
                            stack.append((ii, jj))
                if not touches and cells >= 2:
                    count += 1
    return count


def main():
    data = json.load(open("demo.json"))
    out = glyph.render(data)
    imgs = dict((im["id"], im["coverage"]) for im in out["images"])
    ok = True

    print("== 1. contour distance vs direct-curve reference, hole topology ==")
    for r in data["renders"]:
        segs = glyph_segs(data, r["glyph"])
        refd = ref_distance(segs, r)
        ours = imgs[r["id"]]
        A = contour_from_sign(refd)
        B = contour_from_sign([[0.5 - c for c in row] for row in ours])
        d = chamfer(A, B, trace=True)
        ref = ref_coverage(segs, r)
        h_ref, h_our = hole_components(ref), hole_components(ours)
        good = d <= 1.0 + 1e-9 and h_ref == h_our
        ok &= good
        print("  %-8s bidir contour dist = %.3fpx  holes ref/ours = %d/%d  %s"
              % (r["id"], d, h_ref, h_our, "OK" if good else "FAIL"))

    print("== 2. glyph-order invariance ==")
    shuffled = dict(data)
    shuffled["glyphs"] = list(reversed(data["glyphs"]))
    out2 = glyph.render(shuffled)
    imgs2 = dict((im["id"], im["coverage"]) for im in out2["images"])
    same = all(imgs[k] == imgs2[k] for k in imgs)
    print("  images identical after shuffling glyphs:", same)
    ok &= same

    print("== 3. no reads from neighbouring atlas blocks ==")
    blocks, grid, aw, ah, tpe = glyph.build_atlas(data)
    by_id = dict((b.gid, b) for b in blocks)
    for b in blocks:
        g2 = list(grid)
        for j in range(ah):
            for i in range(aw):
                in_block = (b.rx <= i < b.rx + b.w) and (b.ry <= j < b.ry + b.h)
                if not in_block:
                    g2[j * aw + i] = -123.456  # garbage: would flip coverage
        for r in data["renders"]:
            if r["glyph"] != b.gid:
                continue
            cov = glyph.render_image(r, b, g2, aw, tpe)
            cov = [[round(c, 6) for c in row] for row in cov]
            if cov != imgs[r["id"]]:
                print("  FAIL:", r["id"], "changed when neighbours corrupted")
                ok = False
    print("  neighbour-corruption test passed")

    print("== 4. stroke dilation + sub-pixel shift stability ==")
    r0 = dict(data["renders"][2])  # ring-64
    segs = glyph_segs(data, r0["glyph"])

    def contour_diff(rs, ours):
        refd = ref_distance(segs, rs)
        A = contour_from_sign(refd)
        B = contour_from_sign([[0.5 - c for c in row] for row in ours])
        return chamfer(A, B, trace=True)

    for stroke in (0.02, 0.05):
        rs = dict(r0, stroke=stroke)
        ours = glyph.render_image(rs, by_id[rs["glyph"]], grid, aw, tpe)
        d = contour_diff(rs, ours)
        good = d <= 1.0 + 1e-9
        ok &= good
        print("  stroke=%.2f: contour dist = %.3fpx  %s"
              % (stroke, d, "OK" if good else "FAIL"))
    for dx in (0.31, 0.77):
        rs = dict(r0, origin=[r0["origin"][0] + dx, r0["origin"][1] + dx / 2])
        ours = glyph.render_image(rs, by_id[rs["glyph"]], grid, aw, tpe)
        d = contour_diff(rs, ours)
        ref = ref_coverage(segs, rs)
        h_ref, h_our = hole_components(ref), hole_components(ours)
        good = d <= 1.0 + 1e-9 and h_ref == h_our
        ok &= good
        print("  shift=%.2f: contour dist = %.3fpx holes %d/%d  %s"
              % (dx, d, h_ref, h_our, "OK" if good else "FAIL"))

    print("== 5. atlas blocks non-overlapping ==")
    rects = [(b.rx, b.ry, b.rx + b.w, b.ry + b.h) for b in blocks]
    overlap = False
    for a in range(len(rects)):
        for c in range(a + 1, len(rects)):
            A, B = rects[a], rects[c]
            if A[0] < B[2] and B[0] < A[2] and A[1] < B[3] and B[1] < A[3]:
                overlap = True
    print("  overlap:", overlap)
    ok &= not overlap

    print("ALL OK" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
