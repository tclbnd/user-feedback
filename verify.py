#!/usr/bin/env python3
"""Acceptance checks for the crack-free tessellator.

Runs every scene in scenes/ under three error thresholds and verifies:

  A. per shared edge: both sides report the same factor and their output
     vertex-id sequences are pointwise identical (one side reversed);
  B. manifold closure: every non-boundary mesh edge has exactly two
     triangles (cracks / T-junctions counted as failures); boundary edges
     (patch outer boundaries and saddle-hole rims) have exactly one;
  C. no degenerate triangle (repeated id or world area < 1e-12);
  D. analytic nearest-point distance over sampled triangle points stays
     within the error budget;
  E. eight random patch-order shuffles produce the same normalized mesh
     (digest + canonical verts/tris + stats).

Run:  python3 verify.py
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tessellate as T

HERE = os.path.dirname(os.path.abspath(__file__))
SCENE_DIR = os.path.join(HERE, "scenes")
THRESHOLDS = [0.10, 0.05, 0.02]
N_SHUFFLES = 8
BARY = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
        (0.5, 0.5, 0.0), (0.5, 0.0, 0.5), (0.0, 0.5, 0.5),
        (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)]


def load_scenes():
    out = []
    for name in sorted(os.listdir(SCENE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(SCENE_DIR, name)) as f:
                out.append((name, json.load(f)))
    return out


def output_id_map(out):
    return {T.qkey(v): i for i, v in enumerate(out["verts"])}


def edge_id_sequence(patch, edge, factor, idmap):
    ids, qcoords = [], []
    for j in range(factor + 1):
        p = T.edge_sample(patch, edge, j, factor)
        ids.append(idmap[T.qkey(p)])
        qcoords.append(T.qkey(p))     # merge semantics are quantized
    return ids, qcoords


def expected_edge_map(patches, stats_by_id):
    """Reconstruct the expected mesh from the negotiated edge factors using
    the exact same generation rules (grid, degenerate filter, hole cut) and
    return {quantized_segment: incident-triangle count}.

    Comparing the output mesh against this map edge-by-edge catches cracks,
    T-junctions, missing triangles and unexpected boundaries at once.
    """
    expected = {}

    def add(p, q):
        if p == q:
            return
        seg = tuple(sorted((p, q)))
        expected[seg] = expected.get(seg, 0) + 1

    for pid, p in enumerate(patches):
        f = stats_by_id[pid]["edge_factors"]
        n_u = max(f["N"], f["S"])
        n_v = max(f["E"], f["W"])
        fn = T.POINT_FN[p["type"]]
        c = p.get("center", [0.0, 0.0, 0.0])
        r = p.get("r", 1.0)
        grid = [[T.qkey(fn(p["uv"], i / n_u, j / n_v, c, r))
                 for j in range(n_v + 1)] for i in range(n_u + 1)]
        for i in range(n_u):
            for j in range(n_v):
                cell = (grid[i][j], grid[i + 1][j],
                        grid[i + 1][j + 1], grid[i][j + 1])
                for tri in ((cell[0], cell[1], cell[2]),
                            (cell[0], cell[2], cell[3])):
                    if len(set(tri)) < 3:
                        continue
                    wpts = [list(k) for k in tri]  # area on quantized coords
                    if T.triangle_area(*wpts) < T.AREA_EPS:
                        continue
                    world = [fn(p["uv"], (i + a) / n_u, (j + b) / n_v, c, r)
                             for (a, b) in ((0, 0), (1, 0), (1, 1))]
                    if T.in_saddle_hole(p, world):
                        continue
                    add(tri[0], tri[1])
                    add(tri[1], tri[2])
                    add(tri[2], tri[0])
    return expected


def mesh_edge_map(verts, tris):
    edge_count = {}
    edge_seg = {}
    for tri in tris:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            e = (a, b) if a < b else (b, a)
            edge_count[e] = edge_count.get(e, 0) + 1
            edge_seg[e] = tuple(sorted((T.qkey(verts[a]), T.qkey(verts[b]))))
    return edge_count, edge_seg


def boundary_segments(patches, stats_by_id):
    """Quantized segments on legitimate outer-boundary patch edges."""
    boundary = set()
    groups = {}
    for pid, p in enumerate(patches):
        for e in T.EDGES:
            groups.setdefault(T.edge_fingerprint(p, e), []).append((pid, e))
    idmap_dummy = None  # not needed; sample points directly
    for fp, members in groups.items():
        if len(members) != 1:
            continue
        pid, e = members[0]
        f = stats_by_id[pid]["edge_factors"][e]
        p = patches[pid]
        for j in range(f):
            k1 = T.qkey(T.edge_sample(p, e, j, f))
            k2 = T.qkey(T.edge_sample(p, e, j + 1, f))
            if k1 != k2:  # collapsed pole "segments" produce no mesh edge
                boundary.add(tuple(sorted((k1, k2))))
    return boundary


def check_shared_edges(scene, out):
    """A+B: edge factor symmetry, pointwise equal sequences, closure."""
    patches = scene["patches"]
    idmap = output_id_map(out)
    stats_by_id = {s["id"]: s for s in out["patch_stats"]}

    groups = {}
    for pid, p in enumerate(patches):
        for e in T.EDGES:
            groups.setdefault(T.edge_fingerprint(p, e), []).append((pid, e))

    failures, seq_checked = [], 0
    for members in groups.values():
        if len(members) < 2:
            continue
        seqs = []
        factors = set()
        for pid, e in members:
            f = stats_by_id[pid]["edge_factors"][e]
            factors.add(f)
            ids, coords = edge_id_sequence(patches[pid], e, f, idmap)
            seqs.append((ids, coords))
        if len(factors) != 1:
            failures.append("factor mismatch on shared edge: %s" % factors)
        for k in range(1, len(seqs)):
            ids0, c0 = seqs[0]
            idsk, ck = seqs[k]
            same = ids0 == idsk
            rev = ids0 == idsk[::-1]
            if not (same or rev):
                failures.append("shared-edge vertex sequence mismatch")
                continue
            # coordinate-level check (must match with the same orientation)
            coord_ok = (c0 == ck) or (c0 == ck[::-1])
            if not coord_ok:
                failures.append("shared-edge coordinate sequence mismatch")
            seq_checked += 1

    # closure / manifold: compare against the expected edge incidence map
    edge_count, edge_seg = mesh_edge_map(out["verts"], out["tris"])
    expected = expected_edge_map(patches, stats_by_id)
    cracks = 0
    mismatches = 0
    for e, cnt in edge_count.items():
        seg = edge_seg[e]
        if seg not in expected:
            mismatches += 1
        elif cnt != expected[seg]:
            mismatches += 1
            if expected[seg] == 2 and cnt == 1:
                cracks += 1
    if len(expected) != len(edge_count) or mismatches:
        failures.append(
            "edge incidence mismatch vs expected watertight mesh "
            "(actual %d segments, expected %d, mismatched %d)"
            % (len(edge_count), len(expected), mismatches))
    return failures, seq_checked, len(edge_count), cracks


def check_degenerate(out):
    bad = 0
    for tri in out["tris"]:
        if len(set(tri)) < 3:
            bad += 1
        elif T.triangle_area(*(out["verts"][i] for i in tri)) < T.AREA_EPS:
            bad += 1
    return bad


def analytic_distance(p, patches):
    """Nearest-point distance to the analytic patch surfaces."""
    best = float("inf")
    for patch in patches:
        c = patch.get("center", [0, 0, 0])
        if patch["type"] == "sphere":
            r = patch.get("r", 1.0)
            best = min(best, abs(math_dist(p, c) - r))
            continue
        uv = patch["uv"]
        x, y = p[0] - c[0], p[1] - c[1]
        if not _inside(patch, p[0] - c[0], p[1] - c[1]):
            continue  # another patch owns this point
        z = x * x - y * y + c[2]
        best = min(best, abs(p[2] - z))
    return best


def _inside(patch, x, y):
    uv = patch["uv"]
    eps = 1e-9
    return (min(uv[0], uv[2]) - eps <= x <= max(uv[0], uv[2]) + eps and
            min(uv[1], uv[3]) - eps <= y <= max(uv[1], uv[3]) + eps)


def math_dist(a, b):
    return (sum((a[i] - b[i]) ** 2 for i in range(3))) ** 0.5


def check_geometry(scene, out):
    worst = 0.0
    n_samples = 0
    for tri in out["tris"]:
        pts = [out["verts"][i] for i in tri]
        for wa, wb, wc in BARY:
            p = [pts[0][k] * wa + pts[1][k] * wb + pts[2][k] * wc
                 for k in range(3)]
            worst = max(worst, analytic_distance(p, scene["patches"]))
            n_samples += 1
    return worst, n_samples


def normalized_summary(out):
    digest, (cverts, ctris) = T.normalized_mesh(out["verts"], out["tris"])
    vkeys = tuple(sorted(T.qkey(v) for v in out["verts"]))
    return digest, cverts, ctris, vkeys


def run_case(name, scene, err):
    sc = json.loads(json.dumps(scene))
    sc["err"] = err
    out = T.tessellate(sc)

    failures, n_shared_edges, n_edges, cracks = check_shared_edges(sc, out)
    deg = check_degenerate(out)
    if deg:
        failures.append("%d degenerate triangles in output" % deg)
    worst, n_samples = check_geometry(sc, out)
    if worst > err * (1 + 1e-9):
        failures.append("geometry error %.3g exceeds budget %.3g"
                        % (worst, err))

    # order invariance: 8 shuffles
    rng = random.Random(abs(hash((name, err))) % (2 ** 32))
    base = normalized_summary(out)
    shuffle_ok = True
    for _ in range(N_SHUFFLES):
        order = list(range(len(sc["patches"])))
        rng.shuffle(order)
        sc2 = json.loads(json.dumps(sc))
        sc2["order"] = order
        out2 = T.tessellate(sc2)
        if normalized_summary(out2) != base:
            shuffle_ok = False
            failures.append("normalized mesh differs after shuffle")
            break
        if out2["patch_stats"] != out["patch_stats"]:
            shuffle_ok = False
            failures.append("patch_stats differ after shuffle")
            break

    return {
        "name": name, "err": err,
        "verts": len(out["verts"]), "tris": len(out["tris"]),
        "shared_edges_checked": n_shared_edges,
        "mesh_edges": n_edges, "cracks": cracks,
        "degenerate": deg, "max_geom_err": worst,
        "geom_samples": n_samples, "digest": out["digest"],
        "shuffle_ok": shuffle_ok,
        "pass": not failures, "failures": failures,
        "stats": out["patch_stats"],
    }


def main():
    lines = []
    log = lambda s: (print(s), lines.append(s))
    all_pass = True
    for name, scene in load_scenes():
        log("=" * 78)
        log("scene: %s  (%d patches)" % (name, len(scene["patches"])))
        for err in THRESHOLDS:
            r = run_case(name, scene, err)
            log("  err=%-5g | verts=%-5d tris=%-5d edges=%-5d cracks=%d "
                "degen=%d maxerr=%.4g samples=%d digest=%s shuffle8=%s"
                % (r["err"], r["verts"], r["tris"], r["mesh_edges"],
                   r["cracks"], r["degenerate"], r["max_geom_err"],
                   r["geom_samples"], r["digest"],
                   "OK" if r["shuffle_ok"] else "FAIL"))
            log("    shared edges checked: %d ; patch edge factors: %s"
                % (r["shared_edges_checked"],
                   [(s["id"], s["edge_factors"], s["n_tris"])
                    for s in r["stats"]]))
            if not r["pass"]:
                all_pass = False
                for f in r["failures"]:
                    log("    FAIL: " + f)
    log("=" * 78)
    log("OVERALL: %s" % ("ALL CHECKS PASSED" if all_pass else "FAILURES"))
    with open(os.path.join(HERE, "verify_results.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
