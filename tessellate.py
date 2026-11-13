#!/usr/bin/env python3
"""Crack-free adaptive parametric tessellator.

Design (all decisions are made up-front, independent of patch processing order):

1. Per-patch required edge density.  For an edge curve r(t), t in [0,1], the
   uniform chord-error bound is  max|r(t)-chord(t)| <= M / (8 n^2) with
   M = max|r''(t)|.  We use the slightly conservative convention
       n_req = max(1, ceil(sqrt(M / (4 * err))))
   with M evaluated from the *analytic* second derivatives of the parametric
   surface (uv domain span already baked into F(s,t), s,t in [0,1]).
   Interior requirements use max|F_ss| / max|F_tt| over the patch.

2. Shared-edge decision.  World-space edges are matched by a quantized
   fingerprint (33 samples, 1e-9 world quantization, orientation canonicalized
   so reversed parameterizations match).  Every edge group gets one factor
       edge_factor = next_pow2(max(n_req over all incident patch sides))
   Power-of-two rounding means the coarse side's parameter samples are a
   superset alignment of the fine side's for any level difference (2:1, 4:1,
   ...); taking the max makes the two sides identical, not merely 2:1.

3. Vertex identity.  Each patch is tessellated as a structured grid
   n_u = max(N,S factors), n_v = max(E,W factors).  Every sampled point goes
   through a global registry keyed by quantized world coordinates (1e-9), so
   coincident samples on shared edges - including the lon=0/lon=2*pi seam and
   collapsed pole edges - receive exactly the same vertex id, regardless of
   patch processing order or reversed parameter orientation.

4. No degenerate output.  Triangles with a repeated vertex id or world-space
   area < 1e-12 are dropped (this also removes pole collapse triangles).

5. Order-independent digest.  The digest is sha256 over the normalized mesh:
   lexicographically sorted vertices, remapped triangle indices, sorted
   triangles.  Raw vertex ids depend on processing order; the normalized mesh
   does not.

CLI (unchanged):  python3 tessellate.py scene.json output.json
"""
import json
import sys
import math
import hashlib

QUANT = 1e-9              # world-space vertex merge tolerance
FP_SEGMENTS = 32          # samples per edge fingerprint
DERIV_SAMPLES = 64        # samples for curvature upper bounds
AREA_EPS = 1e-12          # degenerate triangle area threshold
HOLE_R = 0.2              # saddle patch: no sampling within this xy radius


# ---------------------------------------------------------------- geometry
def sphere_pt(uv, s, t, c, r):
    """Lon/lat sphere block; s,t normalized, uv domain baked in."""
    u = uv[0] + s * (uv[2] - uv[0])
    v = uv[1] + t * (uv[3] - uv[1])
    lon = u * 2.0 * math.pi
    lat = (v - 0.5) * math.pi
    cl, sl = math.cos(lat), math.sin(lat)
    co, si = math.cos(lon), math.sin(lon)
    return [c[0] + r * cl * co,
            c[1] + r * sl,
            c[2] + r * cl * si]


def saddle_pt(uv, s, t, c, r):
    """Hyperbolic paraboloid z = x^2 - y^2 over an xy rectangle."""
    x = uv[0] + s * (uv[2] - uv[0])
    y = uv[1] + t * (uv[3] - uv[1])
    return [x + c[0], y + c[1], (x * x - y * y) + c[2]]


POINT_FN = {"sphere": sphere_pt, "saddle": saddle_pt}


def sphere_d2(uv, s, t, r):
    """Analytic second derivatives F_ss, F_tt, F_st of the sphere block."""
    u = uv[0] + s * (uv[2] - uv[0])
    v = uv[1] + t * (uv[3] - uv[1])
    a = 2.0 * math.pi * (uv[2] - uv[0])
    b = math.pi * (uv[3] - uv[1])
    lon, lat = u * 2.0 * math.pi, (v - 0.5) * math.pi
    cl, sl = math.cos(lat), math.sin(lat)
    co, si = math.cos(lon), math.sin(lon)
    fss = [r * a * a * (-cl * co), 0.0, r * a * a * (-cl * si)]
    ftt = [r * b * b * (-cl * co), r * b * b * (-sl), r * b * b * (-cl * si)]
    fst = [r * a * b * (sl * si), 0.0, r * a * b * (-sl * co)]
    return fss, ftt, fst


def saddle_d2(uv, s, t, r):
    du = uv[2] - uv[0]
    dv = uv[3] - uv[1]
    return [0.0, 0.0, 2.0 * du * du], [0.0, 0.0, -2.0 * dv * dv], [0.0, 0.0, 0.0]


D2_FN = {"sphere": sphere_d2, "saddle": saddle_d2}


def vnorm(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


# ------------------------------------------------------------- quantization
def qkey(p):
    """Quantized world-space key (merge tolerance QUANT)."""
    return (round(p[0] / QUANT), round(p[1] / QUANT), round(p[2] / QUANT))


def snap(p):
    """Snap a point onto the quantization lattice so that every sample with
    the same key stores bit-identical coordinates (order-independent
    identity, robust across different floating-point evaluation paths such
    as lon=0 vs lon=2*pi or collapsed pole edges)."""
    k = qkey(p)
    return [k[0] * QUANT, k[1] * QUANT, k[2] * QUANT]


class VertexRegistry:
    """Global vertex table: one id per quantized world position."""

    def __init__(self):
        self._key_to_id = {}
        self.verts = []

    def get(self, p):
        k = qkey(p)
        if k not in self._key_to_id:
            self._key_to_id[k] = len(self.verts)
            self.verts.append(snap(p))
        return self._key_to_id[k]


# ------------------------------------------------------------- edge helpers
EDGES = ("N", "S", "E", "W")


def edge_sample(patch, edge, j, n):
    """World point at parameter j/n along a named edge (N:t=1, S:t=0,
    E:s=1, W:s=0)."""
    fn = POINT_FN[patch["type"]]
    c = patch.get("center", [0.0, 0.0, 0.0])
    r = patch.get("r", 1.0)
    t = j / n
    if edge == "S":
        return fn(patch["uv"], t, 0.0, c, r)
    if edge == "N":
        return fn(patch["uv"], t, 1.0, c, r)
    if edge == "W":
        return fn(patch["uv"], 0.0, t, c, r)
    return fn(patch["uv"], 1.0, t, c, r)  # E


def edge_fingerprint(patch, edge):
    """Orientation-independent identity of an edge in world space."""
    key = tuple(qkey(edge_sample(patch, edge, j, FP_SEGMENTS))
                for j in range(FP_SEGMENTS + 1))
    return min(key, key[::-1])


def edge_required_factor(patch, edge, err):
    """Density required on this side of an edge from the chord-error bound."""
    d2 = D2_FN[patch["type"]]
    r = patch.get("r", 1.0)
    uv = patch["uv"]
    m = 0.0
    for k in range(DERIV_SAMPLES + 1):
        t = k / DERIV_SAMPLES
        if edge in ("S", "N"):
            tt = 1.0 if edge == "N" else 0.0
            m = max(m, vnorm(d2(uv, t, tt, r)[0]))      # |F_ss| along edge
        else:
            ss = 1.0 if edge == "E" else 0.0
            m = max(m, vnorm(d2(uv, ss, t, r)[1]))      # |F_tt| along edge
    return required_factor(m, err)


def interior_required(patch, err):
    """Density required inside the patch by surface second derivatives."""
    d2 = D2_FN[patch["type"]]
    r = patch.get("r", 1.0)
    uv = patch["uv"]
    mu = mv = mm = 0.0
    for i in range(DERIV_SAMPLES + 1):
        s = i / DERIV_SAMPLES
        for j in range(DERIV_SAMPLES + 1):
            t = j / DERIV_SAMPLES
            fss, ftt, fst = d2(uv, s, t, r)
            mu = max(mu, vnorm(fss))
            mv = max(mv, vnorm(ftt))
            mm = max(mm, vnorm(fst))
    # mixed derivative contribution folded conservatively into each axis
    return required_factor(mu + mm, err), required_factor(mv + mm, err)


def required_factor(m, err):
    """Round-up convention for a raw second-derivative bound M."""
    if m <= 0.0:
        return 1
    return max(1, math.ceil(math.sqrt(m / (4.0 * err))))


def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def triangle_area(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def in_saddle_hole(patch, world_pts):
    """True if a triangle centroid falls in the patch's center hole."""
    if patch["type"] != "saddle":
        return False
    uv = patch["uv"]
    center = patch.get("center", [0.0, 0.0, 0.0])
    cx = 0.5 * (uv[0] + uv[2]) + center[0]
    cy = 0.5 * (uv[1] + uv[3]) + center[1]
    x = sum(p[0] for p in world_pts) / 3.0
    y = sum(p[1] for p in world_pts) / 3.0
    return (x - cx) ** 2 + (y - cy) ** 2 < HOLE_R ** 2


# --------------------------------------------------------------- pipeline
def negotiate(patches, err):
    """Pre-pass: edge factor for every (patch, edge), order independent."""
    raw = {}                       # (pid, edge) -> n_req on this side
    groups = {}                    # fingerprint -> [(pid, edge, n_req)]
    for pid, patch in enumerate(patches):
        for e in EDGES:
            n_req = edge_required_factor(patch, e, err)
            raw[(pid, e)] = n_req
            groups.setdefault(edge_fingerprint(patch, e), []).append(
                (pid, e, n_req))

    edge_factor = {}
    for members in groups.values():
        factor = next_pow2(max(n for _, _, n in members))
        for pid, e, _ in members:
            edge_factor[(pid, e)] = factor
    return edge_factor, raw


def tessellate(sc):
    patches = sc["patches"]
    err = float(sc["err"])
    order = sc.get("order") or list(range(len(patches)))

    edge_factor, raw = negotiate(patches, err)
    registry = VertexRegistry()
    tris = []
    stats_by_id = {}

    for pid in order:
        patch = patches[pid]
        fn = POINT_FN[patch["type"]]
        c = patch.get("center", [0.0, 0.0, 0.0])
        r = patch.get("r", 1.0)

        req_u, req_v = interior_required(patch, err)
        # structured grid: N/S edges run in u, E/W edges run in v
        n_u = next_pow2(max(req_u, edge_factor[(pid, "N")],
                            edge_factor[(pid, "S")]))
        n_v = next_pow2(max(req_v, edge_factor[(pid, "E")],
                            edge_factor[(pid, "W")]))

        grid = [[registry.get(fn(patch["uv"], i / n_u, j / n_v, c, r))
                 for j in range(n_v + 1)] for i in range(n_u + 1)]

        n_tris = 0
        for i in range(n_u):
            for j in range(n_v):
                candidates = (
                    (grid[i][j], grid[i + 1][j], grid[i + 1][j + 1]),
                    (grid[i][j], grid[i + 1][j + 1], grid[i][j + 1]),
                )
                for tri in candidates:
                    if len(set(tri)) < 3:
                        continue            # repeated vertex (pole collapse)
                    wpts = [registry.verts[k] for k in tri]
                    if triangle_area(*wpts) < AREA_EPS:
                        continue            # degenerate area
                    if in_saddle_hole(patch, wpts):
                        continue            # interior hole
                    tris.append(list(tri))
                    n_tris += 1

        stats_by_id[pid] = {
            "id": pid,
            "edge_factors": {e: edge_factor[(pid, e)] for e in EDGES},
            "n_tris": n_tris,
        }

    digest, _ = normalized_mesh(registry.verts, tris)
    return {
        "verts": registry.verts,
        "tris": tris,
        "patch_stats": [stats_by_id[i] for i in sorted(stats_by_id)],
        "digest": digest,
    }


def normalized_mesh(verts, tris):
    """Canonical (order-independent) form of a mesh + sha256[:16] digest."""
    perm = sorted(range(len(verts)), key=lambda i: verts[i])
    remap = [0] * len(verts)
    for new_i, old_i in enumerate(perm):
        remap[old_i] = new_i
    canon_verts = [verts[i] for i in perm]
    canon_tris = sorted(tuple(sorted(remap[k] for k in tri)) for tri in tris)
    payload = json.dumps([canon_verts,
                          [list(t) for t in canon_tris]],
                         separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:16], (canon_verts, canon_tris)


def main():
    with open(sys.argv[1]) as f:
        sc = json.load(f)
    out = tessellate(sc)
    with open(sys.argv[2], "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
