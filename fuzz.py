"""Randomized fuzz: atlas-rendered coverage vs exact per-pixel field."""
import math
import random
import time
import glyph
import verify


def rand_contour(rng, cx, cy, rad, n):
    """Random star-ish closed contour with mixed L/Q commands."""
    cmds = []
    angs = sorted(rng.uniform(0, 2 * math.pi) for _ in range(n))
    pts = []
    for a in angs:
        r = rad * rng.uniform(0.5, 1.0)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    cmds.append(["M", pts[0][0], pts[0][1]])
    for k in range(1, len(pts)):
        if rng.random() < 0.4:
            mx = (pts[k - 1][0] + pts[k][0]) / 2
            my = (pts[k - 1][1] + pts[k][1]) / 2
            bx = 2 * (mx + rng.uniform(-0.3, 0.3) * rad) - (pts[k - 1][0] + pts[k][0]) / 2
            by = 2 * (my + rng.uniform(-0.3, 0.3) * rad) - (pts[k - 1][1] + pts[k][1]) / 2
            cmds.append(["Q", bx, by, pts[k][0], pts[k][1]])
        else:
            cmds.append(["L", pts[k][0], pts[k][1]])
    if rng.random() < 0.7:
        cmds.append(["Z"])
    return cmds


def rand_glyph(rng, gid):
    comps = []
    for _ in range(rng.randint(1, 3)):
        ang = rng.uniform(0, 2 * math.pi)
        sx = rng.uniform(0.3, 1.0) * rng.choice([1, -1])
        sy = rng.uniform(0.3, 1.0) * rng.choice([1, -1])
        ca, sa = math.cos(ang), math.sin(ang)
        m = [sx * ca, -sy * sa, sx * sa, sy * ca,
             rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)]
        paths = [rand_contour(rng, 0.0, 0.0, rng.uniform(0.2, 0.5),
                              rng.randint(3, 8))
                 for _ in range(rng.randint(1, 2))]
        comps.append({"transform": m, "paths": paths})
    return {"id": gid, "components": comps}


def make_data():
    rng = random.Random(20261113)
    glyphs = []
    renders = []
    for gi in range(12):
        gid = "g%d" % gi
        glyphs.append(rand_glyph(rng, gid))
        for _ in range(3):
            scale = rng.choice([18, 24, 31, 48, 64, 96])
            renders.append({
                "id": "%s-%d" % (gid, len(renders)),
                "glyph": gid,
                "width": rng.randint(20, 110),
                "height": rng.randint(20, 110),
                "scale": scale,
                "origin": [rng.uniform(2, 8), rng.uniform(2, 8)],
                "stroke": rng.choice([0.0, 0.0, 0.01, 0.03]),
            })
    return {"glyphs": glyphs, "renders": renders,
            "texels_per_em": 64, "padding": 5}


def trace(rid):
    data = make_data()
    out = glyph.render(data)
    imgs = dict((im["id"], im["coverage"]) for im in out["images"])
    r = [x for x in data["renders"] if x["id"] == rid][0]
    segs = verify.glyph_segs(data, r["glyph"])
    refd = verify.ref_distance(segs, r)
    ours = imgs[r["id"]]
    A = verify.contour_from_sign(refd)
    B = verify.contour_from_sign([[0.5 - c for c in row] for row in ours])
    print(rid, "scale", r["scale"], "origin", [round(v, 3) for v in r["origin"]],
          "stroke", r.get("stroke"))
    verify.chamfer(A, B, trace=True)
    return data, r, refd, ours


def main():
    rng = random.Random(20261113)
    glyphs = []
    renders = []
    for gi in range(12):
        gid = "g%d" % gi
        glyphs.append(rand_glyph(rng, gid))
        for _ in range(3):
            scale = rng.choice([18, 24, 31, 48, 64, 96])
            renders.append({
                "id": "%s-%d" % (gid, len(renders)),
                "glyph": gid,
                "width": rng.randint(20, 110),
                "height": rng.randint(20, 110),
                "scale": scale,
                "origin": [rng.uniform(2, 8), rng.uniform(2, 8)],
                "stroke": rng.choice([0.0, 0.0, 0.01, 0.03]),
            })
    data = {"glyphs": glyphs, "renders": renders,
            "texels_per_em": 64, "padding": 5}

    t0 = time.time()
    out = glyph.render(data)
    t1 = time.time()
    print("render time: %.2fs, atlas %dx%d, %d renders"
          % (t1 - t0, out["atlas"]["width"], out["atlas"]["height"], len(renders)))
    imgs = dict((im["id"], im["coverage"]) for im in out["images"])

    worst = 0.0
    fails = 0
    for r in renders:
        segs = verify.glyph_segs(data, r["glyph"])
        refd = verify.ref_distance(segs, r)
        A = verify.contour_from_sign(refd)
        B = verify.contour_from_sign([[0.5 - c for c in row]
                                      for row in imgs[r["id"]]])
        d = verify.chamfer(A, B)
        worst = max(worst, d)
        if d > 1.0 + 1e-9:
            fails += 1
            print("  FAIL %s: dist=%.3f (scale=%s stroke=%s)"
                  % (r["id"], d, r["scale"], r.get("stroke")))
    print("worst contour distance: %.3fpx, failures: %d/%d"
          % (worst, fails, len(renders)))


if __name__ == "__main__":
    main()
