"""Independent ray-cast reference path.

This module deliberately shares no code with :mod:`clipspace` or the
coverage machinery in :mod:`rasterizer`.  Instead of clipping and edge
functions it casts one ray per sample point from the camera and finds the
nearest ray/triangle intersection (Moeller-Trumbore).  It is used to
cross-check the main pipeline's coverage masks and the depth/UV values at
selected sample points.

Conventions match the main renderer: camera at ``(0, 0, distance)``
looking down ``-Z``; a sample point ``(sx, sy)`` in pixel coordinates
maps to the ray direction ``(ndc_x * aspect / f, ndc_y / f, -1)`` so the
ray parameter ``t`` equals view-space depth directly.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import clipspace

Vertex = Tuple[float, float, float]


def _sub(a: Vertex, b: Vertex) -> Vertex:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vertex, b: Vertex) -> Vertex:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _ray_triangle(
    origin: Vertex, direction: Vertex, p0: Vertex, p1: Vertex, p2: Vertex
) -> Optional[Tuple[float, float, float]]:
    """Moeller-Trumbore; returns ``(t, u, v)`` or None."""
    e1 = _sub(p1, p0)
    e2 = _sub(p2, p0)
    h = _cross(direction, e2)
    det = _dot(e1, h)
    if det == 0.0:
        return None
    inv = 1.0 / det
    s = _sub(origin, p0)
    u = inv * _dot(s, h)
    if u < 0.0 or u > 1.0:
        return None
    q = _cross(s, e1)
    v = inv * _dot(direction, q)
    if v < 0.0 or u + v > 1.0:
        return None
    t = inv * _dot(e2, q)
    return (t, u, v)


def sample_ray(
    positions: Sequence[Vertex],
    faces: Sequence[Tuple[Tuple[int, int, int], Tuple[int, int, int]]],
    uvs: Sequence[Tuple[float, float]],
    proj: clipspace.Projection,
    sx: float,
    sy: float,
    cull_backfaces: bool = True,
) -> Tuple[bool, float, float, float, int]:
    """Query one sample point.

    Returns ``(covered, depth, u, v, face_index)``.  Uncovered samples
    return ``(False, far, 0.0, 0.0, -1)``.  Depth ties go to the smallest
    face index, matching the main pipeline's stable primitive order.
    """
    ndc_x = (sx / proj.width) * 2.0 - 1.0
    ndc_y = 1.0 - (sy / proj.height) * 2.0
    direction = (
        ndc_x * proj.aspect / proj.f,
        ndc_y / proj.f,
        -1.0,
    )
    origin: Vertex = (0.0, 0.0, proj.distance)

    best_t = math.inf
    best_face = -1
    best_u = 0.0
    best_v = 0.0

    for face_index, (v_idx, _t_idx) in enumerate(faces):
        p0 = positions[v_idx[0]]
        p1 = positions[v_idx[1]]
        p2 = positions[v_idx[2]]
        if cull_backfaces:
            # Front-facing iff the geometric normal opposes the ray.
            normal = _cross(_sub(p1, p0), _sub(p2, p0))
            if _dot(normal, direction) >= 0.0:
                continue
        hit = _ray_triangle(origin, direction, p0, p1, p2)
        if hit is None:
            continue
        t, hu, hv = hit
        if t < proj.near or t > proj.far:
            continue
        if t < best_t or (t == best_t and (best_face < 0 or face_index < best_face)):
            best_t = t
            best_face = face_index
            best_u = hu
            best_v = hv

    if best_face < 0:
        return (False, proj.far, 0.0, 0.0, -1)

    t_idx = faces[best_face][1]
    tex = []
    for k in range(3):
        t = t_idx[k]
        tex.append(uvs[t] if 0 <= t < len(uvs) else (0.0, 0.0))
    w0 = 1.0 - best_u - best_v
    u = w0 * tex[0][0] + best_u * tex[1][0] + best_v * tex[2][0]
    v = w0 * tex[0][1] + best_u * tex[1][1] + best_v * tex[2][1]
    return (True, best_t, u, v, best_face)


def coverage_mask(
    positions: Sequence[Vertex],
    faces: Sequence[Tuple[Tuple[int, int, int], Tuple[int, int, int]]],
    uvs: Sequence[Tuple[float, float]],
    proj: clipspace.Projection,
    sample_offsets: Sequence[Tuple[float, float]],
    cull_backfaces: bool = True,
) -> List[List[int]]:
    """Full-image per-pixel coverage masks via ray casting (slow, exact)."""
    rows: List[List[int]] = []
    for py in range(proj.height):
        row: List[int] = []
        for px in range(proj.width):
            mask = 0
            for si, (ox, oy) in enumerate(sample_offsets):
                covered, _t, _u, _v, _f = sample_ray(
                    positions, faces, uvs, proj,
                    px + ox, py + oy, cull_backfaces,
                )
                if covered:
                    mask |= 1 << si
            row.append(mask)
        rows.append(row)
    return rows
