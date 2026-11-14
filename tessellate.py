#!/usr/bin/env python3
"""Adaptive parametric tessellator with crack-free stitched boundaries.

Design notes
------------
1. Unified edge factors.  Each patch first estimates a required segment
   count from its own curvature and the error budget.  A subdivision
   factor is then a property of the *edge*, not of one side: for every
   world-space edge we take the maximum requirement over all incident
   patches and round it up to a power of two.  Both sides of a shared
   edge therefore sample it at identical parameters t = k/f and produce
   identical vertex identities.  Any level gap (2:1, 8:1, ...) works
   because every edge factor divides the patch interior resolution.
2. Consistent vertex identity on the coarse side.  The interior
   resolution n of a patch is the smallest power of two that is >= its
   own need and >= all four edge factors.  The outermost ring of cells
   is triangulated as four trapezoid fans between the coarse boundary
   loop and the fine inner rectangle [1..n-1]^2; corner cells are cut by
   the (corner)->(inner corner) diagonal, so coarse/coarse corners stay
   consistent.  Vertices merge by 1e-9 world-space quantization and the
   stored position is the de-quantized canonical key, so the vertex set
   is independent of patch processing order.  Degenerate triangles
   (repeated vertex id, or 3D area < 1e-12) are never emitted.
3. Determinism.  Per-patch statistics are emitted sorted by patch id;
   the digest is computed over the normalized mesh (vertices sorted,
   indices remapped, triangles sorted), so shuffling `order` changes
   neither the vertex set nor the digest.
"""
import json, sys, math, hashlib

QUANT = 1e-9      # world-space vertex merge tolerance
MIN_AREA = 1e-12  # degenerate triangle area threshold
SAFETY = 2.0      # density safety factor on top of the 1/8 sagitta bound
KGRID = 8         # curvature sampling grid for the density estimate

def sphere_pt(uv, u, v, c, r):
    u0, v0, u1, v1 = uv
    lon = (u0 + u * (u1 - u0)) * 2.0 * math.pi
    lat = (v0 + v * (v1 - v0) - 0.5) * math.pi
    return [c[0] + r * math.cos(lat) * math.cos(lon),
            c[1] + r * math.sin(lat),
            c[2] + r * math.cos(lat) * math.sin(lon)]

def saddle_pt(uv, u, v, c, r):
    u0, v0, u1, v1 = uv
    x = u0 + u * (u1 - u0)
    y = v0 + v * (v1 - v0)
    return [x + c[0], y + c[1], r * (x * x - y * y) + c[2]]

PT = {"sphere": sphere_pt, "saddle": saddle_pt}

# edge name -> (corner a, corner b) in unit param coords
EDGES = {"S": ((0.0, 0.0), (1.0, 0.0)),
         "E": ((1.0, 0.0), (1.0, 1.0)),
         "N": ((0.0, 1.0), (1.0, 1.0)),
         "W": ((0.0, 0.0), (0.0, 1.0))}

def qkey(p):
    """Quantized world-space key (1e-9 tolerance)."""
    return (int(round(p[0] / QUANT)),
            int(round(p[1] / QUANT)),
            int(round(p[2] / QUANT)))

def pow2ceil(x):
    x = max(1, int(math.ceil(x)))
    return 1 << (x - 1).bit_length()

def _sd2(a, b, c):
    """Norm of the second difference a - 2b + c."""
    return math.sqrt(sum((a[k] - 2.0 * b[k] + c[k]) ** 2 for k in range(3)))

def required_n(p, err):
    """Segments this patch would need on its own (curvature vs budget)."""
    f = PT[p["type"]]
    uv = p["uv"]; c = p.get("center", [0, 0, 0]); r = p.get("r", 1)
    K = KGRID
    g = [[f(uv, i / K, j / K, c, r) for j in range(K + 1)]
         for i in range(K + 1)]
    d2 = 0.0
    for i in range(1, K):
        for j in range(K + 1):
            d2 = max(d2, _sd2(g[i - 1][j], g[i][j], g[i + 1][j]))
    for j in range(1, K):
        for i in range(K + 1):
            d2 = max(d2, _sd2(g[i][j - 1], g[i][j], g[i][j + 1]))
    if d2 <= 0.0:
        return 1
    # linear-interpolation error ~ d2 * (K/n)^2 / 8  <=  err
    return max(1, int(math.ceil(SAFETY * K * math.sqrt(d2 / (8.0 * err)))))

def tessellate(sc):
    patches = sc["patches"]
    err = sc["err"]
    order = sc.get("order", list(range(len(patches))))

    reqs = [required_n(p, err) for p in patches]

    # ---- unified edge factors -----------------------------------------
    edge_key = {}   # (patch idx, edge name) -> canonical endpoint-pair key
    incident = {}   # edge key -> set of patch indices sharing the edge
    for idx, p in enumerate(patches):
        f = PT[p["type"]]; uv = p["uv"]
        c = p.get("center", [0, 0, 0]); r = p.get("r", 1)
        for name, (ca, cb) in EDGES.items():
            pa = f(uv, ca[0], ca[1], c, r)
            pb = f(uv, cb[0], cb[1], c, r)
            key = tuple(sorted((qkey(pa), qkey(pb))))
            edge_key[(idx, name)] = key
            incident.setdefault(key, set()).add(idx)
    # max incident requirement, rounded up to a power of two
    factor_of = {k: pow2ceil(max(reqs[i] for i in v))
                 for k, v in incident.items()}

    # ---- mesh accumulation --------------------------------------------
    verts, vmap = [], {}
    def vid(p):
        k = qkey(p)
        i = vmap.get(k)
        if i is None:
            i = len(verts)
            vmap[k] = i
            # canonical de-quantized position: order independent
            verts.append([k[0] * QUANT, k[1] * QUANT, k[2] * QUANT])
        return i

    tris = []
    stats = []
    for idx in order:
        p = patches[idx]
        f = PT[p["type"]]; uv = p["uv"]
        c = p.get("center", [0, 0, 0]); r = p.get("r", 1)
        u0, v0, u1, v1 = uv
        fac = {name: factor_of[edge_key[(idx, name)]] for name in EDGES}
        # interior resolution: power of two, multiple of every edge factor
        n = max([pow2ceil(reqs[idx])] + [fac[e] for e in EDGES])

        hole_r = 0.0
        h = p.get("hole")
        if h:
            hole_r = float(h) if isinstance(h, (int, float)) else 0.2
        xc, yc = 0.5 * (u0 + u1), 0.5 * (v0 + v1)

        cache = {}
        def gvid(i, j):
            key = (i, j)
            v = cache.get(key)
            if v is None:
                v = vid(f(uv, i / n, j / n, c, r))
                cache[key] = v
            return v

        cnt = 0
        def emit(a, b, cc):
            nonlocal cnt
            # exact param-space degenerate test (integer grid coords)
            if ((b[0] - a[0]) * (cc[1] - a[1])
                    - (b[1] - a[1]) * (cc[0] - a[0])) == 0:
                return
            ia, ib, ic = gvid(*a), gvid(*b), gvid(*cc)
            if ia == ib or ib == ic or ia == ic:   # repeated vertex
                return
            pa, pb, pc = verts[ia], verts[ib], verts[ic]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            wx, wy, wz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx = uy * wz - uz * wy
            ny = uz * wx - ux * wz
            nz = ux * wy - uy * wx
            if 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz) < MIN_AREA:
                return                             # degenerate area
            tris.append([ia, ib, ic])
            cnt += 1

        def cell_in_hole(i, j):
            if hole_r <= 0.0:
                return False
            cx = u0 + (i + 0.5) / n * (u1 - u0)
            cy = v0 + (j + 0.5) / n * (v1 - v0)
            return (cx - xc) ** 2 + (cy - yc) ** 2 < hole_r * hole_r

        if n == 1:
            emit((0, 0), (1, 0), (1, 1))
            emit((0, 0), (1, 1), (0, 1))
        else:
            # interior: regular grid over cells [1..n-2]^2
            for i in range(1, n - 1):
                for j in range(1, n - 1):
                    if cell_in_hole(i, j):
                        continue
                    emit((i, j), (i + 1, j), (i + 1, j + 1))
                    emit((i, j), (i + 1, j + 1), (i, j + 1))
            # annulus ring: four trapezoid fans, coarse loop -> fine rect
            fS, fE, fN, fW = fac["S"], fac["E"], fac["N"], fac["W"]
            rS, rE, rN, rW = n // fS, n // fE, n // fN, n // fW
            polys = [
                [(k * rS, 0) for k in range(fS + 1)]
                    + [(i, 1) for i in range(n - 1, 0, -1)],
                [(n, k * rE) for k in range(fE + 1)]
                    + [(n - 1, j) for j in range(n - 1, 0, -1)],
                [(n - k * rN, n) for k in range(fN + 1)]
                    + [(i, n - 1) for i in range(1, n)],
                [(0, n - k * rW) for k in range(fW + 1)]
                    + [(1, j) for j in range(n - 1, 0, -1)],
            ]
            for poly in polys:
                apex = poly[-1]                    # inner-rect corner
                for k in range(len(poly) - 2):
                    emit(apex, poly[k], poly[k + 1])

        stats.append({"id": idx,
                      "edge_factors": {"N": fac["N"], "S": fac["S"],
                                       "E": fac["E"], "W": fac["W"]},
                      "n_tris": cnt})

    stats.sort(key=lambda s: s["id"])

    # ---- normalized digest (order independent) -------------------------
    idx_order = sorted(range(len(verts)), key=lambda i: verts[i])
    remap = {old: new for new, old in enumerate(idx_order)}
    norm_verts = [verts[i] for i in idx_order]
    norm_tris = sorted(tuple(sorted(remap[t] for t in tri)) for tri in tris)
    dig = hashlib.sha256(repr([norm_verts, norm_tris]).encode()).hexdigest()[:16]

    return {"verts": verts, "tris": tris, "patch_stats": stats,
            "digest": dig}

def main():
    sc = json.load(open(sys.argv[1]))
    json.dump(tessellate(sc), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
