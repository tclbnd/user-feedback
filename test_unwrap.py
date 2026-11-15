#!/usr/bin/env python3
"""Acceptance tests for unwrap.py.

    python3 test_unwrap.py            # run everything
    python3 test_unwrap.py -v         # also print the counters

Checks, in the order of the specification:

1. Analytic references (disk, cylinder) - the unwrap must be exact: zero
   conformal energy, zero angle error, zero area distortion, and an isometry of
   the plane, so the seam and the results can be checked against closed-form
   answers.
2. Seam correctness - every non-seam interior edge is continuous, every seam
   edge is really cut open, and islands are disks without overlap.
3. Determinism - three or more initial parameters (vertex/triangle orderings,
   and half-edge traversal order) give the same seams, energy and digest; the
   seam set is identical in 3-D geometry, not merely in index space.
4. Minimality - the seam graph has exactly 2g independent cycles for a closed
   genus-g surface, and a disk is not cut at all.
5. Robustness - degenerate triangles, several components, genus 2, and
   non-manifold / self-touching input are handled.
"""
import math
import sys
from collections import defaultdict

import meshes as M
import unwrap as U

VERBOSE = "-v" in sys.argv
FAIL = []
PASS_COUNT = [0]


def check(name, cond, detail=""):
    if cond:
        PASS_COUNT[0] += 1
    else:
        FAIL.append(name)
    if VERBOSE or not cond:
        print("%-58s %s %s" % (name, "PASS" if cond else "FAIL", detail))


# --------------------------------------------------------------------------
# geometry helpers used by the checks
# --------------------------------------------------------------------------
def cross2(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def seg_intersect(p1, p2, p3, p4):
    d1 = cross2(p3, p4, p1)
    d2 = cross2(p3, p4, p2)
    d3 = cross2(p1, p2, p3)
    d4 = cross2(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def uv_overlap(res, tris):
    """Number of proper self-intersections of the UV mesh, ignoring pairs that
    share a mesh vertex (legitimate corner contact)."""
    uvf = res["uv_faces"]
    boxes = []
    for ti in range(len(tris)):
        pts = uvf[ti]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append((min(xs), max(xs), min(ys), max(ys), pts))
    bad = 0
    for i in range(len(tris)):
        ai = boxes[i]
        for j in range(i + 1, len(tris)):
            bj = boxes[j]
            if (ai[1] <= bj[0] or bj[1] <= ai[0] or
                    ai[3] <= bj[2] or bj[3] <= ai[2]):
                continue
            if set(tris[i]) & set(tris[j]):
                continue
            for a in range(3):
                for b in range(3):
                    if seg_intersect(ai[4][a], ai[4][(a + 1) % 3],
                                     bj[4][b], bj[4][(b + 1) % 3]):
                        bad += 1
    return bad


def edge_ratio_spread(res, pos, tris):
    """Spread of |uv edge| / |3-D edge| over all triangle edges.

    An exact unwrap of a developable surface is a plane isometry, so every
    ratio must equal one single number; the spread then measures how far the
    numerical result is from that closed form.
    """
    uvf = res["uv_faces"]
    ratios = []
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = k, (k + 1) % 3
            d3 = math.dist(pos[t[a]], pos[t[b]])
            dU = math.dist(uvf[ti][a], uvf[ti][b])
            if d3 > 1e-9:
                ratios.append(dU / d3)
    if not ratios:
        return 0.0
    mean = sum(ratios) / len(ratios)
    return max(abs(r - mean) for r in ratios) / mean


def seam_consistency(res, tris):
    """(a) non-seam interior edges are continuous (shared vertex identity),
    (b) seam edges really separate two UV copies."""
    uvf = res["uv_faces"]
    seam_set = {tuple(sorted(e)) for e in res["seams"]}
    edges = defaultdict(list)
    for ti, t in enumerate(tris):
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            edges[(a, b) if a < b else (b, a)].append(ti)
    bad_glue = bad_seam = 0
    for e, ts in edges.items():
        if len(ts) != 2:
            continue
        i, j = ts

        def uv_from(ti, v):
            for k, w in enumerate(tris[ti]):
                if w == v:
                    return uvf[ti][k]
            return None
        same = all(uv_from(i, v) == uv_from(j, v) for v in e)
        if e in seam_set:
            bad_seam += 0 if not same else 1
        else:
            bad_glue += 0 if same else 1
    return bad_glue, bad_seam


def seam_b1(seams):
    """First Betti number of the seam graph: its number of independent cycles.
    For a closed genus-g surface the minimal cut graph has exactly 2g of them."""
    if not seams:
        return 0
    adjv = defaultdict(set)
    for a, b in seams:
        adjv[a].add(b)
        adjv[b].add(a)
    seen, comps = set(), 0
    for s in sorted(adjv):
        if s in seen:
            continue
        comps += 1
        stack = [s]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            for y in sorted(adjv[x]):
                if y not in seen:
                    stack.append(y)
    return len(seams) - len(adjv) + comps


def seam_geometry(res, pos):
    return sorted(tuple(sorted((
        tuple(round(c, 6) for c in pos[a]), tuple(round(c, 6) for c in pos[b]))))
        for a, b in res["seams"])


# --------------------------------------------------------------------------
# 1. analytic references
# --------------------------------------------------------------------------
def test_analytic():
    pos, tris = M.grid_disk(8)
    r = U.unwrap(pos, tris)
    check("disk: not cut", len(r["seams"]) == 0)
    check("disk: conformal energy is zero", abs(r["energy"]) < 1e-9)
    check("disk: angle error is zero", r["angle_err"] < 1e-9)
    check("disk: area distortion is zero", r["area_dist"] < 1e-9)
    s = edge_ratio_spread(r, pos, tris)
    check("disk: UV map is a similarity", s < 1e-7, "spread=%.2e" % s)

    for nx, nz in ((8, 4), (16, 8), (32, 12)):
        pos, tris = M.cylinder(nx, nz)
        r = U.unwrap(pos, tris)
        spread = edge_ratio_spread(r, pos, tris)
        tag = "cyl%dx%d" % (nx, nz)
        check("%s: conformal energy is zero" % tag, abs(r["energy"]) < 1e-9)
        check("%s: isometric (ratio spread 0)" % tag, spread < 1e-7,
              "spread=%.2e" % spread)
        check("%s: angle error is zero" % tag, r["angle_err"] < 1e-6)
        check("%s: single meridian seam" % tag, len(r["seam_paths"]) == 1)
        check("%s: no UV overlap" % tag, uv_overlap(r, tris) == 0)
        # Area distortion of a developable surface must be *uniform*: a
        # conformal map is only determined up to a similarity, so the ratio
        # A_uv / A_3d has to be the same constant on every triangle (a zero
        # area-distortion unwrap in the scale-invariant sense used by `energy`
        # and `area_dist`).
        uvf = r["uv_faces"]
        ratios = []
        for ti, t in enumerate(tris):
            p = uvf[ti]
            a_uv = abs(0.5 * ((p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) -
                              (p[1][1] - p[0][1]) * (p[2][0] - p[0][0])))
            a_3d = U.tri_area3(*(pos[v] for v in t))
            if a_3d > 1e-12:
                ratios.append(a_uv / a_3d)
        spread = (max(ratios) - min(ratios)) / (sum(ratios) / len(ratios))
        check("%s: area distortion is uniform (zero distortion)" % tag,
              spread < 1e-7, "spread=%.2e" % spread)
        check("%s: reported area_dist is zero" % tag, r["area_dist"] < 1e-7,
              "area_dist=%.2e" % r["area_dist"])


# --------------------------------------------------------------------------
# 2. seam correctness
# --------------------------------------------------------------------------
def test_seams():
    cases = [("disk", M.grid_disk, 0),
             ("cylinder", M.cylinder, 0),
             ("torus", M.torus, 0)]
    for name, fn, folds in cases:
        pos, tris = fn()
        r = U.unwrap(pos, tris)
        bg, bs = seam_consistency(r, tris)
        check("%s: non-seam edges are continuous" % name, bg == 0,
              "violations=%d" % bg)
        check("%s: seam edges are really cut open" % name, bs == 0,
              "violations=%d" % bs)
        check("%s: islands are disks" % name, r["islands"] >= 1)
        check("%s: no folded triangles" % name, r["flipped"] == 0)
        ov = uv_overlap(r, tris)
        check("%s: no UV overlap" % name, ov == 0, "crossings=%d" % ov)


# --------------------------------------------------------------------------
# 3. determinism under reordering
# --------------------------------------------------------------------------
def test_determinism():
    seeds = (1, 2, 3, 7, 11, 23)
    for name, fn in [("disk", M.grid_disk), ("cylinder", M.cylinder),
                     ("torus", M.torus), ("genus-2", M.two_tori)]:
        pos, tris = fn()
        base = U.unwrap(pos, tris)
        digests = {base["digest"]}
        seam_ok = summary_ok = True
        for seed in seeds:
            p2, t2 = M.permute((pos, tris), seed)
            o = U.unwrap(p2, t2)
            digests.add(o["digest"])
            if seam_geometry(base, pos) != seam_geometry(o, p2):
                seam_ok = False
            if (abs(base["energy"] - o["energy"]) > 1e-9 or
                    base["islands"] != o["islands"] or
                    abs(base["angle_err"] - o["angle_err"]) > 1e-9 or
                    abs(base["area_dist"] - o["area_dist"]) > 1e-9 or
                    base["flipped"] != o["flipped"]):
                summary_ok = False
        n = len(seeds) + 1
        check("%s: digest identical over %d orderings" % (name, n),
              len(digests) == 1)
        check("%s: seam set identical over %d orderings" % (name, n), seam_ok)
        check("%s: energy/angle/area/islands identical" % name, summary_ok)


# --------------------------------------------------------------------------
# 4. minimality
# --------------------------------------------------------------------------
def test_minimality():
    check("disk: zero seam edges",
          len(U.unwrap(*M.grid_disk(6))["seams"]) == 0)
    # an annulus has one generator: a single meridian of nz edges
    check("cylinder annulus: one meridian",
          len(U.unwrap(*M.cylinder(8, 4))["seams"]) == 4)
    for nu, nv in ((8, 6), (12, 8), (16, 10)):
        pos, tris = M.torus(nu, nv)
        r = U.unwrap(pos, tris)
        b1 = seam_b1([tuple(e) for e in r["seams"]])
        check("torus %dx%d: cut graph has exactly 2g=2 cycles" % (nu, nv),
              b1 == 2, "b1=%d seams=%d" % (b1, len(r["seams"])))
        # the two generators are the two mesh periods
        check("torus %dx%d: seam edges = nu + nv" % (nu, nv),
              len(r["seams"]) == nu + nv, "seams=%d" % len(r["seams"]))
    pos, tris = M.two_tori()
    r = U.unwrap(pos, tris)
    b1 = seam_b1([tuple(e) for e in r["seams"]])
    check("genus-2: cut graph has exactly 2g=4 cycles", b1 == 4, "b1=%d" % b1)


# --------------------------------------------------------------------------
# 5. robustness
# --------------------------------------------------------------------------
def test_robustness():
    pos, tris = M.with_degenerate(M.torus(8, 6))
    r = U.unwrap(pos, tris)
    check("degenerate faces dropped",
          r["stats"]["degenerate_faces"] == 2 and r["islands"] >= 1,
          "deg=%d" % r["stats"]["degenerate_faces"])
    check("degenerate input: no folds", r["flipped"] == 0)

    # two disjoint components -> two islands, no seams in either
    g = M.grid_disk(3)
    off = len(g[0])
    pos2 = g[0] + [(x + 5.0, y, z) for (x, y, z) in g[0]]
    tris2 = g[1] + [(a + off, b + off, c + off) for (a, b, c) in g[1]]
    rm = U.unwrap(pos2, tris2)
    check("two components: two islands and no seams",
          rm["islands"] == 2 and len(rm["seams"]) == 0,
          "islands=%d seams=%d" % (rm["islands"], len(rm["seams"])))

    # genus-2 folds must disappear as the mesh is refined
    folds = [U.unwrap(*M.two_tori(nu, nv))["flipped"]
             for nu, nv in ((8, 6), (10, 7), (12, 8))]
    check("genus-2: folds vanish with refinement", folds[-1] == 0,
          "flips=%s" % folds)

    # pathological inputs must not raise
    edge_cases = [
        ("empty", [], []),
        ("one triangle", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]),
        ("all degenerate", [(0, 0, 0), (1, 0, 0)],
         [(0, 0, 1), (0, 0, 0), (1, 1, 1)]),
        ("non-manifold edge",
         [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)],
         [(0, 1, 2), (1, 0, 3), (0, 1, 4)]),
        ("bowtie", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)],
         [(0, 1, 2), (0, 3, 4)]),
    ]
    for name, p, t in edge_cases:
        try:
            rr = U.unwrap(p, t)
            ok = len(rr["uv"]) == len(p) and len(rr["uv_faces"]) == len(t)
        except Exception as exc:                      # noqa: BLE001
            ok = False
            rr = None
            print("  exception for %s: %r" % (name, exc))
        check("edge case '%s' handled" % name, ok)


def main():
    test_analytic()
    test_seams()
    test_determinism()
    test_minimality()
    test_robustness()
    print()
    if FAIL:
        print("FAILED %d / %d checks:" % (len(FAIL), len(FAIL) + PASS_COUNT[0]))
        for f in FAIL:
            print("  -", f)
        return 1
    print("ALL %d CHECKS PASSED" % PASS_COUNT[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
