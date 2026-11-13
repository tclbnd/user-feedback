"""Sloped test mesh: shared edges, duplicated seam vertices and mirrored UV.

The mesh is a height-field slope z = a*x + b*y cut into two charts
along a vertical seam.  Each chart duplicates the seam vertices (the
real-world UV-unwelding scenario) but keeps identical smooth normals on
both sides, so a correct baker reproduces the same irradiance across
the seam.  The second chart may be emitted with mirrored UVs and a
reversed winding; rasterisation is winding-agnostic and records the
mirror flag per triangle.
"""
from .vecmath import cross, norm3, sub


def _grid_chart(cid, x0, x1, cols, rows, size, a, b, mirror, z0=0.0):
    verts = []
    uvs = []
    for j in range(rows + 1):
        for i in range(cols + 1):
            tx = i / cols
            ty = j / rows
            x = x0 + (x1 - x0) * tx
            y = ty
            z = z0 + a * (x - x0) + b * ty
            verts.append((x, y, z))
            uvs.append((1.0 - tx, ty) if mirror else (tx, ty))

    def vid(i, j):
        return j * (cols + 1) + i

    # Base winding gives +z-ish geometric normals; mirrored UVs reverse
    # the UV winding, which the rasteriser reports automatically.
    base = [(0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1)]
    tris = []
    for j in range(rows):
        for i in range(cols):
            corner = vid(i, j)
            idx = [corner + (di + dj * (cols + 1)) for di, dj in base]
            p = [verts[k] for k in idx]
            nrm = norm3(cross(sub(p[1], p[0]), sub(p[2], p[0])))
            tris.append({
                "uv": [list(uvs[k]) for k in idx],
                "normal": [list(nrm)] * 3,
                "positions": [list(p[0]), list(p[1]), list(p[2])],
            })
    return {
        "id": cid, "size": size, "origin": [0, 0], "rotate": 0,
        "triangles": tris,
    }


def build_slope(size=32, mirror=True, cols=8, rows=8):
    """Two-chart sloped mesh sharing a geometric seam.

    Returns dict(charts, seam, edge_report) where seam lists matching
    (chart, uv, position, normal) samples on both sides of the cut and
    edge_report classifies every grid edge.
    """
    a, b = 0.35, -0.2
    left = _grid_chart("L", 0.0, 0.5, max(2, cols // 2), rows,
                       size, a, b, mirror=False)
    right = _grid_chart("R", 0.5, 1.0, max(2, cols // 2), rows,
                        size, a, b, mirror=mirror)
    # Smooth analytic normals of the height field: n = (-a,-b,1) normalised.
    smooth = norm3((-a, -b, 1.0))
    for ch in (left, right):
        for t in ch["triangles"]:
            t["normal"] = [list(smooth)] * 3

    # Seam samples: same 3D positions, two duplicated UV vertices.
    # The mirrored right chart keeps its geometric left edge at u=1.
    seam = []
    for j in range(rows + 1):
        y = j / rows
        pos = [0.5, y, a * 0.5 + b * y]
        seam.append({
            "position": pos,
            "normal": list(smooth),
            "left_uv": [1.0, y],
            "right_uv": [1.0, y] if mirror else [0.0, y],
        })

    edge_report = _edge_report(left, right, mirror)
    return {"charts": [left, right], "seam": seam, "edge_report": edge_report,
            "smooth_normal": list(smooth)}


def _edge_report(left, right, mirror):
    """Classify edges: internal shared edges vs boundary seam edges."""
    def edges(chart):
        out = set()
        for t in chart["triangles"]:
            uv = [(round(u, 9), round(v, 9)) for u, v in t["uv"]]
            for k in range(3):
                e = tuple(sorted((uv[k], uv[(k + 1) % 3])))
                out.add(e)
        return out
    le, re = edges(left), edges(right)
    shared = le & re
    return {
        "left_edges": len(le),
        "right_edges": len(re),
        "identical_uv_edges": len(shared),
        "right_chart_mirrored": bool(mirror),
        "note": "seam vertices are duplicated; mirrored winding handled per triangle",
    }
