"""Homogeneous clip-space projection and six-plane frustum clipping.

Conventions
-----------
The camera sits at ``(0, 0, distance)`` and looks down ``-Z``.  A world
point ``p`` becomes the view-space point ``(x, y, z - distance)`` and is
mapped to clip space with a standard OpenGL-style perspective matrix::

    x_c = f / aspect * x_v
    y_c = f * y_v
    z_c = z_a * z_v + z_b
    w_c = -z_v                      (== view-space depth)

with ``f = 1 / tan(fov/2)``, ``z_a = -(far + near) / (far - near)`` and
``z_b = -2 * far * near / (far - near)``.  A point is inside the frustum
iff ``-w <= x, y, z <= w``; the near test ``z_c + w_c >= 0`` is exactly
``view_depth >= near``, so every clipped vertex has ``w_c >= near > 0``
and the perspective divide is always finite.

Clipping is Sutherland-Hodgman against the six planes in homogeneous
space.  All vertex attributes (position and UV) are interpolated linearly
in clip space along clipped edges; perspective correctness is recovered
later by interpolating ``u/w``, ``v/w`` and ``1/w`` in screen space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

# (x, y, z, w, u, v): homogeneous position plus per-vertex UV.
ClipVertex = Tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class Projection:
    width: int
    height: int
    fov_deg: float
    distance: float
    near: float
    far: float
    f: float        # 1 / tan(fov / 2)
    aspect: float   # width / height
    z_a: float
    z_b: float


def make_projection(
    width: int,
    height: int,
    fov_deg: float = 45.0,
    distance: float = 5.0,
    near: float = 0.25,
    far: float = 500.0,
) -> Projection:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if distance <= 0.0:
        raise ValueError("camera distance must be positive")
    if not 0.0 < fov_deg < 180.0:
        raise ValueError("fov must be between 0 and 180 degrees")
    if not 0.0 < near < far:
        raise ValueError("need 0 < near < far")
    f = 1.0 / math.tan(math.radians(fov_deg) * 0.5)
    z_a = -(far + near) / (far - near)
    z_b = -2.0 * far * near / (far - near)
    return Projection(
        width=width,
        height=height,
        fov_deg=float(fov_deg),
        distance=float(distance),
        near=float(near),
        far=float(far),
        f=f,
        aspect=float(width) / float(height),
        z_a=z_a,
        z_b=z_b,
    )


def world_to_view(point: Sequence[float], proj: Projection) -> Tuple[float, float, float]:
    return (point[0], point[1], point[2] - proj.distance)


def view_to_clip(point: Sequence[float], proj: Projection) -> Tuple[float, float, float, float]:
    x_v, y_v, z_v = point
    return (
        proj.f / proj.aspect * x_v,
        proj.f * y_v,
        proj.z_a * z_v + proj.z_b,
        -z_v,
    )


def world_to_clip(
    point: Sequence[float], u: float, v: float, proj: Projection
) -> ClipVertex:
    x, y, z, w = view_to_clip(world_to_view(point, proj), proj)
    return (x, y, z, w, u, v)


# Signed distances to the six frustum planes; >= 0 means inside.
def _plane_distances(x: float, y: float, z: float, w: float) -> Tuple[float, ...]:
    return (
        x + w,  # left:   x >= -w
        w - x,  # right:  x <=  w
        y + w,  # bottom: y >= -w
        w - y,  # top:    y <=  w
        z + w,  # near:   z >= -w  (view depth >= near)
        w - z,  # far:    z <=  w  (view depth <= far)
    )


# Vertices this close to a plane (relative to their magnitude) count as
# inside.  Without the tolerance, a vertex that is mathematically on a
# frustum plane (e.g. a shared edge exactly on the boundary) can be
# clipped away by a 1-ulp rounding error, and each adjacent triangle
# would clip to slightly different intersection points.
_CLIP_TOL = 1e-9


def _intersect(a: ClipVertex, b: ClipVertex, da: float, db: float) -> ClipVertex:
    """Edge/plane intersection; attributes interpolated linearly in clip space."""
    t = da / (da - db)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return (
        a[0] + t * (b[0] - a[0]),
        a[1] + t * (b[1] - a[1]),
        a[2] + t * (b[2] - a[2]),
        a[3] + t * (b[3] - a[3]),
        a[4] + t * (b[4] - a[4]),
        a[5] + t * (b[5] - a[5]),
    )


def _clip_plane(poly: List[ClipVertex], plane: int) -> List[ClipVertex]:
    out: List[ClipVertex] = []
    count = len(poly)
    dists = [_plane_distances(v[0], v[1], v[2], v[3])[plane] for v in poly]
    tols = [
        _CLIP_TOL * (1.0 + abs(v[0]) + abs(v[1]) + abs(v[2]) + abs(v[3]))
        for v in poly
    ]
    inside = [dists[i] >= -tols[i] for i in range(count)]
    for i in range(count):
        j = (i + 1) % count
        if inside[i]:
            out.append(poly[i])
            if not inside[j]:
                out.append(_intersect(poly[i], poly[j], dists[i], dists[j]))
        elif inside[j]:
            out.append(_intersect(poly[i], poly[j], dists[i], dists[j]))
    return out


def clip_polygon(vertices: Sequence[ClipVertex]) -> List[ClipVertex]:
    """Clip a convex polygon against all six frustum planes."""
    poly = list(vertices)
    for plane in range(6):
        if not poly:
            break
        poly = _clip_plane(poly, plane)
    return poly


def clip_triangle(vertices: Sequence[ClipVertex]) -> List[List[ClipVertex]]:
    """Clip one triangle; returns a fan of clipped triangles (possibly empty)."""
    poly = clip_polygon(vertices)
    if len(poly) < 3:
        return []
    return [[poly[0], poly[i], poly[i + 1]] for i in range(1, len(poly) - 1)]
