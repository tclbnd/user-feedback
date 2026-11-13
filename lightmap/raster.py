"""Rasterise chart triangles into a canonical, packing-independent grid.

For every pixel centre (i+0.5, j+0.5)/N in chart-local [0,1]^2 we compute
barycentric coordinates and interpolate the unit normal.  Coverage,
position (barycentric weights) and normal are all recorded so downstream
outputs are fully traceable.

Boundary convention (kept stable on purpose): a pixel centre is inside a
triangle when every barycentric weight is >= -EPS.  When several
triangles claim the same centre (shared edges, duplicated vertices) the
lowest triangle index wins, so input ordering only affects genuine
overlaps, never the per-chart canonical arrays produced by the pipeline.
"""
from .vecmath import bary2d, norm3

INSIDE_EPS = 1e-10


def raster_chart(chart):
    """Return coverage for one chart on its size*size canonical grid.

    Output (indexed by flat index y*N+x):
      covered[yN+x] = dict(tri, bary, normal, mirrored) or None
    Triangle winding is irrelevant; mirrored records a negative
    UV-space signed area (a mirrored UV chart).
    """
    n = int(chart["size"])
    tris = chart.get("triangles", [])
    covered = [None] * (n * n)
    uv_tris = []
    for t in tris:
        uvs = [tuple(p) for p in t["uv"]]
        nrm = [tuple(p) for p in t["normal"]]
        signed = ((uvs[1][0] - uvs[0][0]) * (uvs[2][1] - uvs[0][1])
                  - (uvs[1][1] - uvs[0][1]) * (uvs[2][0] - uvs[0][0]))
        uv_tris.append((uvs, nrm, signed))

    for ti, (uvs, nrms, signed) in enumerate(uv_tris):
        if abs(signed) <= 1e-15:
            # Degenerate UV triangle: ignored per contract.
            continue
        # Conservative pixel bbox of the triangle.
        u0 = min(p[0] for p in uvs)
        u1 = max(p[0] for p in uvs)
        v0 = min(p[1] for p in uvs)
        v1 = max(p[1] for p in uvs)
        x_lo = max(0, int((u0 * n) - 1e-9) - 1)
        x_hi = min(n - 1, int((u1 * n) - 1e-9) + 1)
        y_lo = max(0, int((v0 * n) - 1e-9) - 1)
        y_hi = min(n - 1, int((v1 * n) - 1e-9) + 1)
        for y in range(y_lo, y_hi + 1):
            for x in range(x_lo, x_hi + 1):
                idx = y * n + x
                if covered[idx] is not None:
                    continue
                p = ((x + 0.5) / n, (y + 0.5) / n)
                w = bary2d(p, uvs[0], uvs[1], uvs[2])
                if w is None:
                    continue
                if w[0] < -INSIDE_EPS or w[1] < -INSIDE_EPS or w[2] < -INSIDE_EPS:
                    continue
                w = tuple(max(0.0, k) for k in w)
                interp = (
                    w[0] * nrms[0][0] + w[1] * nrms[1][0] + w[2] * nrms[2][0],
                    w[0] * nrms[0][1] + w[1] * nrms[1][1] + w[2] * nrms[2][1],
                    w[0] * nrms[0][2] + w[1] * nrms[1][2] + w[2] * nrms[2][2],
                )
                covered[idx] = {
                    "tri": ti,
                    "bary": (round(w[0], 12), round(w[1], 12), round(w[2], 12)),
                    "normal": norm3(interp),
                    "mirrored": signed < 0.0,
                }
    return covered
