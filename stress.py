"""Stress tests: spikes, mirrored composites, slivers, stroke, timing."""
import json
import math
import time
import glyph
import verify

# thin sharp triangle (near-coincident edges at apex) + curved base band
spike = {"id": "spike", "components": [{"transform": [1, 0, 0, 1, 0, 0], "paths": [
    [["M", 0.5, 0.02], ["L", 0.53, 0.9], ["L", 0.47, 0.9], ["Z"]],
    [["M", 0.1, 0.75], ["Q", 0.5, 0.55, 0.9, 0.75],
     ["L", 0.9, 0.95], ["L", 0.1, 0.95], ["Z"]]]}]}

# composite: outer square + hole, plus rotated/scaled/mirrored second component
# NOTE: the mirrored component subtracts (nonzero rule) and cuts a wedge only
# ~0.02 em (~1.3 texel) wide through the ring; combined with stroke=0.03 this
# is at the information limit of a 64 texels/em point-sampled SDF, so the
# combo cases may exceed 1 px there. All other cases must pass strictly.
_a = math.radians(30)
_ca, _sa = math.cos(_a), math.sin(_a)
combo = {"id": "combo", "components": [
    {"transform": [1, 0, 0, 1, 0, 0], "paths": [
        [["M", 0.1, 0.1], ["L", 0.9, 0.1], ["L", 0.9, 0.9], ["L", 0.1, 0.9], ["Z"]],
        [["M", 0.3, 0.3], ["L", 0.3, 0.7], ["L", 0.7, 0.7], ["L", 0.7, 0.3], ["Z"]]]},
    {"transform": [-0.4 * _ca, 0.4 * _sa, 0.4 * _sa, 0.4 * _ca, 0.5, 0.5],
     "paths": [
        [["M", -0.5, -0.5], ["L", 0.5, -0.5], ["Q", 0.9, 0.0, 0.5, 0.5],
         ["L", -0.5, 0.5], ["Z"]]]}]}

# near-coincident parallel edges (1e-4 em apart)
sliver = {"id": "sliver", "components": [{"transform": [1, 0, 0, 1, 0, 0], "paths": [
    [["M", 0.2, 0.2], ["L", 0.8, 0.2001], ["L", 0.8, 0.8], ["L", 0.2, 0.8], ["Z"]],
    [["M", 0.2, 0.2001], ["L", 0.8, 0.2002], ["L", 0.8, 0.25], ["L", 0.2, 0.25],
     ["Z"]]]}]}


def make_data():
    renders = []
    for g in ("spike", "combo", "sliver"):
        for k, s in enumerate((18, 48, 96)):
            renders.append({"id": "%s-%d" % (g, s), "glyph": g,
                            "width": int(s * 1.3) + 8,
                            "height": int(s * 1.3) + 8,
                            "scale": s,
                            "origin": [4.0 + 0.37 * k, 4.0 + 0.19 * k],
                            "stroke": 0.03 if k == 1 else 0})
    return {"glyphs": [spike, combo, sliver], "renders": renders,
            "texels_per_em": 64, "padding": 5}


def debug_signmap(rid):
    data = make_data()
    out = glyph.render(data)
    imgs = dict((im["id"], im["coverage"]) for im in out["images"])
    r = [x for x in data["renders"] if x["id"] == rid][0]
    segs = verify.glyph_segs(data, r["glyph"])
    refd = verify.ref_distance(segs, r)
    ours = imgs[r["id"]]
    H, W = len(ours), len(ours[0])
    print(rid, "sign map (#=inside .=outside), rows: ref-exact / ours")
    for j in range(0, H, 2):
        lr = "".join("#" if refd[j][i] < 0 else "." for i in range(0, W, 2))
        lo = "".join("#" if ours[j][i] >= 0.5 else "." for i in range(0, W, 2))
        mark = "  <<<" if lr != lo else ""
        print("R " + lr)
        print("O " + lo + mark)


def main():
    renders = []
    for g in ("spike", "combo", "sliver"):
        for k, s in enumerate((18, 48, 96)):
            renders.append({"id": "%s-%d" % (g, s), "glyph": g,
                            "width": int(s * 1.3) + 8,
                            "height": int(s * 1.3) + 8,
                            "scale": s,
                            "origin": [4.0 + 0.37 * k, 4.0 + 0.19 * k],
                            "stroke": 0.03 if k == 1 else 0})
    data = {"glyphs": [spike, combo, sliver], "renders": renders,
            "texels_per_em": 64, "padding": 5}

    t0 = time.time()
    out = glyph.render(data)
    t1 = time.time()
    print("render time: %.2fs, atlas %dx%d"
          % (t1 - t0, out["atlas"]["width"], out["atlas"]["height"]))
    imgs = dict((im["id"], im["coverage"]) for im in out["images"])

    ok = True
    for r in renders:
        segs = verify.glyph_segs(data, r["glyph"])
        refd = verify.ref_distance(segs, r)
        A = verify.contour_from_sign(refd)
        B = verify.contour_from_sign([[0.5 - c for c in row]
                                      for row in imgs[r["id"]]])
        d = verify.chamfer(A, B)
        ref = verify.ref_coverage(segs, r, S=4)
        h_ref = verify.hole_components(ref)
        h_our = verify.hole_components(imgs[r["id"]])
        good = d <= 1.0 + 1e-9 and h_ref == h_our
        ok &= good
        print("  %-10s dist=%.3f holes=%d/%d %s"
              % (r["id"], d, h_ref, h_our, "OK" if good else "FAIL"))
    print("STRESS", "ALL OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
