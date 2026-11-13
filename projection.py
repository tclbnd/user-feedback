"""Curved-screen multi-projector calibration, edge blending and replay.

Pipeline:
1. Per-projector geometric calibration: independent quadratic models
   screen_x(u,v) / screen_y(u,v) over basis (1,u,v,u^2,uv,v^2), fitted by
   least squares from the surviving calibration markers.
2. Coverage: each output pixel carries a unified surface coordinate (u,v).
   Blocked pixels and pixels outside the surface grid get zero weight.
   Initial weight = distance to the projector's surface rectangle + 1,
   normalized per surface grid point across all covering projectors
   (a lone projector therefore gets weight 1 everywhere it covers).
3. Photometric compensation in the linear luminance domain: with target P
   and active black-level sum B, projector i ideally emits
   black_i + weight_i * max(0, P - B); the playable signal applies gain and
   gamma. Disabled pixels output signal 0 and contribute no black level.
"""
import argparse
import json
import math
from pathlib import Path


def basis(u, v):
    return (1.0, u, v, u * u, u * v, v * v)


def solve6(matrix, vector):
    """Solve a small linear system by Gaussian elimination.

    Raises ValueError on a (numerically) singular system, i.e. a degenerate
    marker layout.
    """
    n = len(vector)
    a = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) <= 1e-12:
            raise ValueError("degenerate marker layout")
        a[col], a[piv] = a[piv], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            if f:
                for c in range(col, n + 1):
                    a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (a[r][n] - sum(a[r][c] * x[c] for c in range(r + 1, n))) / a[r][r]
    return x


def fit_projector(markers):
    """Fit quadratic surface->screen models; return (x_coeffs, y_coeffs, rms)."""
    pts = []
    ata = [[0.0] * 6 for _ in range(6)]
    bx = [0.0] * 6
    by = [0.0] * 6
    for m in markers or []:
        try:
            u, v = float(m["surface"][0]), float(m["surface"][1])
            sx, sy = float(m["screen"][0]), float(m["screen"][1])
        except (KeyError, TypeError, IndexError, ValueError):
            continue  # missing / malformed grid points are simply omitted
        phi = basis(u, v)
        pts.append((phi, sx, sy))
        for i in range(6):
            pi = phi[i]
            for j in range(6):
                ata[i][j] += pi * phi[j]
            bx[i] += pi * sx
            by[i] += pi * sy
    if len(pts) < 6:
        raise ValueError("fewer than 6 valid markers")
    cx = solve6(ata, bx)
    cy = solve6(ata, by)
    err = 0.0
    for phi, sx, sy in pts:
        dx = sum(c * p for c, p in zip(cx, phi)) - sx
        dy = sum(c * p for c, p in zip(cy, phi)) - sy
        err += dx * dx + dy * dy
    return cx, cy, math.sqrt(err / len(pts))


def edge_distance(u, v, u0, u1, v0, v1):
    """Distance from (u,v) to the boundary of rectangle [u0,u1]x[v0,v1]."""
    du = min(u - u0, u1 - u)
    dv = min(v - v0, v1 - v)
    if du >= 0.0 and dv >= 0.0:
        return min(du, dv)
    ox = max(u0 - u, 0.0, u - u1)
    ov = max(v0 - v, 0.0, v - v1)
    return math.hypot(ox, ov)


def compose(data):
    pattern = data["pattern"]
    if not pattern or not pattern[0]:
        raise ValueError("empty pattern")
    h = len(pattern)
    w = len(pattern[0])
    for row in pattern:
        if len(row) != w:
            raise ValueError("ragged pattern")
    projectors = data["projectors"]
    if not projectors:
        raise ValueError("no projectors")

    # 1. geometric calibration, reported sorted by projector id
    calibration = []
    for proj in projectors:
        cx, cy, rms = fit_projector(proj.get("markers"))
        calibration.append({"id": proj["id"], "x": cx, "y": cy, "rms": rms})
    calibration.sort(key=lambda c: c["id"])

    # 2. coverage pass: validity, grid point and initial weight per pixel
    totals = {}   # (gx,gy) -> sum of initial weights over all projectors
    active = {}   # (gx,gy) -> set of covering projector indices
    pinfo = []    # per projector, per pixel: (gx,gy,w0) or None
    for idx, proj in enumerate(projectors):
        pixels = proj["pixels"]
        blocked = proj.get("blocked")
        if blocked is not None:
            if len(blocked) != len(pixels) or any(
                    len(br) != len(pr) for br, pr in zip(blocked, pixels)):
                raise ValueError("blocked mask shape mismatch")
        us = [float(p[0]) for row in pixels for p in row]
        vs = [float(p[1]) for row in pixels for p in row]
        if not us:
            raise ValueError("empty pixel map")
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        info = []
        for r, row in enumerate(pixels):
            irow = []
            for c, p in enumerate(row):
                if blocked is not None and blocked[r][c]:
                    irow.append(None)
                    continue
                u, v = float(p[0]), float(p[1])
                gx = int(math.floor(u + 0.5))
                gy = int(math.floor(v + 0.5))
                if not (0 <= gx < w and 0 <= gy < h):
                    irow.append(None)  # outside the surface grid
                    continue
                w0 = edge_distance(u, v, u0, u1, v0, v1) + 1.0
                key = (gx, gy)
                totals[key] = totals.get(key, 0.0) + w0
                active.setdefault(key, set()).add(idx)
                irow.append((gx, gy, w0))
            info.append(irow)
        pinfo.append(info)

    # 3. photometric pass: normalized weights, signals, combined luminance
    blacks = [float(p.get("black", 0.0)) for p in projectors]
    contrib = {}  # (gx,gy) -> {projector idx: [sum_actual, count]}
    out_projectors = []
    for idx, proj in enumerate(projectors):
        gain = float(proj["gain"])
        gamma = float(proj["gamma"])
        black = blacks[idx]
        if gain <= 0.0 or gamma <= 0.0:
            raise ValueError("gain and gamma must be positive")
        signal = []
        weights = []
        for r, row in enumerate(proj["pixels"]):
            srow = []
            wrow = []
            for c in range(len(row)):
                cell = pinfo[idx][r][c]
                if cell is None:
                    srow.append(0.0)  # disabled pixel: no signal, no black
                    wrow.append(0.0)
                    continue
                gx, gy, w0 = cell
                key = (gx, gy)
                wgt = w0 / totals[key]
                bsum = sum(blacks[j] for j in sorted(active[key]))
                target = max(0.0, float(pattern[gy][gx]) - bsum)
                light = black + wgt * target
                s = min(max((light - black) / gain, 0.0), 1.0) ** (1.0 / gamma)
                actual = black + gain * s ** gamma
                srow.append(s)
                wrow.append(wgt)
                slot = contrib.setdefault(key, {}).setdefault(idx, [0.0, 0])
                slot[0] += actual
                slot[1] += 1
            signal.append(srow)
            weights.append(wrow)
        out_projectors.append(
            {"id": proj["id"], "signal": signal, "weights": weights})

    combined = []
    for gy in range(h):
        row = []
        for gx in range(w):
            per = contrib.get((gx, gy))
            if not per:
                row.append(None)  # invalid: no valid coverage
            else:
                row.append(sum(v[0] / v[1] for v in per.values()))
        combined.append(row)
    return {"projectors": out_projectors, "combined": combined,
            "calibration": calibration}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    try:
        output = compose(json.loads(a.input.read_text()))
        a.output.write_text(json.dumps(output, sort_keys=True, allow_nan=False) + "\n")
    except (ValueError, IndexError, KeyError) as err:
        p.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
