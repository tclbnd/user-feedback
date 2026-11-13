"""Minimal 2D geometry for explicit prism/box validation scenes.

The offline renderer in ``renderer.py`` replays pre-recorded boundary
events; the analytic acceptance tests (prism dispersion, diffuse box) need
real intersection testing instead, which this module provides without any
third-party dependency.

Conventions: points are (x, y) tuples in scene units; directions are unit
vectors; polygon edges store an outward normal (nx, ny).
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from medium import AIR_IOR, fresnel_unpolarized, norm_angle_deg

Point = Tuple[float, float]
Vec = Tuple[float, float]


def unit_from_angle(deg: float) -> Vec:
    r = math.radians(deg)
    return (math.cos(r), math.sin(r))


def angle_of(v: Vec) -> float:
    return norm_angle_deg(math.degrees(math.atan2(v[1], v[0])))


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _sub(a: Point, b: Point) -> Vec:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Vec) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s)


def polygon_normals(points: Sequence[Point]) -> List[Vec]:
    """Outward unit normals for a counter-clockwise polygon."""
    n = len(points)
    out = []
    signed_area2 = 0.0
    for i in range(n):
        p, q = points[i], points[(i + 1) % n]
        signed_area2 += p[0] * q[1] - q[0] * p[1]
    ccw = signed_area2 > 0.0
    for i in range(n):
        p, q = points[i], points[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        if ccw:  # outward normal of a CCW edge is the right hand side
            nrm = (dy, -dx)
        else:
            nrm = (-dy, dx)
        ln = math.hypot(nrm[0], nrm[1])
        out.append((nrm[0] / ln, nrm[1] / ln))
    return out


def ray_polygon_intersect(origin: Point, direction: Vec,
                          points: Sequence[Point],
                          normals: Sequence[Vec],
                          skip: Optional[int] = None,
                          inside: bool = False):
    """Nearest forward ray/polygon-edge intersection.

    Returns (t, edge_index, point, normal) or None.  When tracing from
    outside, edges whose ray points away from the surface (dot(d, n) >= 0)
    are ignored; when tracing from inside, the exit face has its outward
    normal pointing along the ray, so the sign test is reversed.
    """
    best = None
    for i, (p, q) in enumerate(zip(points, points[1:] + points[:1])):
        if i == skip:
            continue
        nrm = normals[i]
        denom = _dot(direction, nrm)
        if inside:
            if denom <= 1e-12:
                continue  # exit face must have outward normal along ray
        else:
            if denom >= -1e-12:
                continue  # entry face must face the ray
        t = _dot(_sub(p, origin), nrm) / denom
        if t <= 1e-9:
            continue
        hit = _add(origin, _scale(direction, t))
        # Confirm the hit lies on the segment (in 2D, on the supporting line
        # between endpoints with a small tolerance).
        ex, ey = q[0] - p[0], q[1] - p[1]
        elen2 = ex * ex + ey * ey
        u = ((hit[0] - p[0]) * ex + (hit[1] - p[1]) * ey) / elen2
        if u < -1e-9 or u > 1.0 + 1e-9:
            continue
        if best is None or t < best[0]:
            best = (t, i, hit, nrm)
    return best


def refract_vector(d: Vec, n: Vec, eta_i: float, eta_t: float):
    """Vector form of Snell's law; returns (direction, tir).

    ``n`` is the geometric (outward) normal.  For entry the ray faces it;
    for exit the ray travels with it, so it is flipped internally.
    """
    n = n if _dot(d, n) < 0.0 else (-n[0], -n[1])
    cos_i = -_dot(d, n)
    eta = eta_i / eta_t
    sin2_t = eta * eta * max(0.0, 1.0 - cos_i * cos_i)
    if sin2_t > 1.0:
        r = (d[0] + 2.0 * cos_i * n[0], d[1] + 2.0 * cos_i * n[1])
        return r, True
    cos_t = math.sqrt(1.0 - sin2_t)
    t = (_scale(d, eta), _scale(n, eta * cos_i - cos_t))
    td = (t[0][0] + t[1][0], t[0][1] + t[1][1])
    ln = math.hypot(td[0], td[1])
    return (td[0] / ln, td[1] / ln), False


def trace_polygon(origin: Point, angle_deg: float,
                  polygon: Sequence[Point], ior_by_wave: Sequence[float],
                  max_bounces: int = 16):
    """Trace one ray through a closed dielectric polygon per wavelength.

    Returns a list, indexed by wavelength, of dicts with the exit point,
    exit angle, accumulated geometric path length inside the polygon, list
    of (eta_i, eta_t, cos_i) Fresnel samples, and any TIR bounces.
    """
    pts = list(polygon)
    normals = polygon_normals(pts)
    results = []
    for wi, eta_t0 in enumerate(ior_by_wave):
        pos = origin
        d = unit_from_angle(angle_deg)
        inside = False
        path_len = 0.0
        fresnel_log = []
        tir_bounces = []
        skip = None
        ok = True
        for _ in range(max_bounces):
            hit = ray_polygon_intersect(pos, d, pts, normals, skip,
                                        inside=inside)
            if hit is None:
                ok = False
                break
            t, edge, hpos, nrm = hit
            eta_i = AIR_IOR if not inside else float(ior_by_wave[wi])
            eta_t = float(ior_by_wave[wi]) if not inside else AIR_IOR
            cos_i = abs(_dot(d, nrm))
            fresnel_log.append((eta_i, eta_t, cos_i,
                                fresnel_unpolarized(eta_i, eta_t, cos_i)))
            nd, tir = refract_vector(d, nrm, eta_i, eta_t)
            if tir:
                # Reflect and continue inside; geometry and medium unchanged.
                d = (d[0] + 2.0 * cos_i * nrm[0],
                     d[1] + 2.0 * cos_i * nrm[1])
                tir_bounces.append(edge)
                pos = _add(hpos, _scale(d, 1e-7))
                skip = edge
                continue
            if inside:
                path_len += t
            d = nd
            pos = _add(hpos, _scale(d, 1e-7))
            inside = not inside
            skip = edge
            if not inside:
                break
        results.append(dict(exit_point=pos, exit_angle=angle_of(d),
                            path_length=path_len, fresnel=fresnel_log,
                            tir_bounces=tir_bounces, ok=ok))
    return results


def equilateral_prism(center: Point, side: float,
                      rotation_deg: float = 0.0) -> List[Point]:
    """Equilateral triangle centred on ``center`` with given side length."""
    r = side / math.sqrt(3.0)
    rot = math.radians(rotation_deg)
    pts = []
    for k in range(3):
        a = rot + math.radians(90.0 + 120.0 * k)
        pts.append((center[0] + r * math.cos(a),
                    center[1] + r * math.sin(a)))
    return pts
