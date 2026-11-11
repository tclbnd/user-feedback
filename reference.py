"""Small independent reference path for validation.

This module deliberately does **not** import :mod:`rasterizer` and implements
no polygon clipping.  Instead it casts one ray per sample through the camera,
intersects the original (unclipped) world-space triangles with
Möller-Trumbore, and keeps the nearest intersection in ``(near, far]``.  A
triangle crossing the near plane is therefore handled by the ray itself, not
by shared clipping code.

The reference is intentionally brute force: it is meant for small images
(typically 16x16 to 48x48) where it provides ground truth for coverage masks,
depth and UV values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from geometry import Triangle, Vec2, coerce_triangles

NEAR_PLANE = 1e-6
FAR_PLANE = 1e9

REFERENCE_SAMPLE_OFFSETS_1 = ((0.5, 0.5),)
REFERENCE_SAMPLE_OFFSETS_4 = (
    (0.25, 0.25),
    (0.75, 0.25),
    (0.25, 0.75),
    (0.75, 0.75),
)

_BARY_EPS = 1e-9
_PARALLEL_EPS = 1e-15
_DEPTH_REL_EPS = 1e-12


@dataclass
class ReferenceResult:
    width: int
    height: int
    samples: int
    coverage: List[List[int]]
    depth: List[List[Optional[float]]]
    uv: List[List[Optional[Vec2]]]
    primitive_ids: List[List[Optional[int]]]
    sample_depths: List[List[List[Optional[float]]]]
    sample_uvs: List[List[List[Optional[Vec2]]]]
    near: float
    far: float


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _intersect(eye, direction, triangle: Triangle):
    """Return ``(t, uv)`` or ``None`` for a ray/triangle intersection.

    ``t`` is measured along *direction*, whose forward component is ``-1``,
    so it equals the view-space depth of the hit point.
    """
    p0, p1, p2 = triangle.p0, triangle.p1, triangle.p2
    edge1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    edge2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])

    h = _cross(direction, edge2)
    a = _dot(edge1, h)
    if abs(a) <= _PARALLEL_EPS:
        return None
    inv_a = 1.0 / a
    s = (eye[0] - p0[0], eye[1] - p0[1], eye[2] - p0[2])
    u = _dot(s, h) * inv_a
    if u < -_BARY_EPS or u > 1.0 + _BARY_EPS:
        return None

    q = _cross(s, edge1)
    v = _dot(direction, q) * inv_a
    if v < -_BARY_EPS or u + v > 1.0 + _BARY_EPS:
        return None

    t = _dot(edge2, q) * inv_a
    if t <= 0.0 or not math.isfinite(t):
        return None

    has_uv = (
        triangle.uv0 is not None
        and triangle.uv1 is not None
        and triangle.uv2 is not None
    )
    if has_uv:
        w0 = 1.0 - u - v
        uv = (
            w0 * triangle.uv0[0] + u * triangle.uv1[0] + v * triangle.uv2[0],
            w0 * triangle.uv0[1] + u * triangle.uv1[1] + v * triangle.uv2[1],
        )
    else:
        uv = None
    return t, uv


def render_reference(
    triangles: Sequence,
    width: int = 24,
    height: Optional[int] = None,
    distance: float = 5.0,
    fov_deg: float = 45.0,
    near: float = NEAR_PLANE,
    far: float = FAR_PLANE,
    samples: int = 1,
) -> ReferenceResult:
    """Ray-cast *triangles* into a small coverage/depth/UV reference image."""
    if width <= 0:
        raise ValueError("width must be positive")
    if height is None:
        height = width
    if height <= 0:
        raise ValueError("height must be positive")
    if samples not in (1, 4):
        raise ValueError("samples must be 1 or 4")
    if near <= 0.0 or far <= near:
        raise ValueError("invalid near/far planes")

    scene = coerce_triangles(triangles)
    offsets = (
        REFERENCE_SAMPLE_OFFSETS_1 if samples == 1 else REFERENCE_SAMPLE_OFFSETS_4
    )
    tan_half = math.tan(math.radians(fov_deg) * 0.5)
    aspect = float(width) / float(height)
    eye = (0.0, 0.0, float(distance))

    coverage = [[0] * width for _ in range(height)]
    depth = [[None] * width for _ in range(height)]
    uv = [[None] * width for _ in range(height)]
    primitive_ids = [[None] * width for _ in range(height)]

    sample_depth: List[List[List[Optional[float]]]] = [
        [[None] * width for _ in range(height)] for _ in range(samples)
    ]
    sample_uvs: List[List[List[Optional[Vec2]]]] = [
        [[None] * width for _ in range(height)] for _ in range(samples)
    ]

    for py in range(height):
        for px in range(width):
            best_depth = math.inf
            best_uv: Optional[Vec2] = None
            best_pid: Optional[int] = None
            best_sample = -1
            mask = 0

            for sample_index, (offset_x, offset_y) in enumerate(offsets):
                sample_x = px + offset_x
                sample_y = py + offset_y
                ndc_x = 2.0 * sample_x / width - 1.0
                ndc_y = 1.0 - 2.0 * sample_y / height
                direction = (
                    ndc_x * tan_half * aspect,
                    ndc_y * tan_half,
                    -1.0,
                )

                hit_depth = math.inf
                hit_uv: Optional[Vec2] = None
                hit_pid: Optional[int] = None
                for pid, triangle in enumerate(scene):
                    result = _intersect(eye, direction, triangle)
                    if result is None:
                        continue
                    t, hit = result
                    if t < near or t > far:
                        continue
                    # Ascending pid order and a depth tolerance make an
                    # effectively equal depth deterministic: the lower pid
                    # wins.
                    tolerance = _DEPTH_REL_EPS * max(1.0, hit_depth)
                    if hit_pid is None or t < hit_depth - tolerance:
                        hit_depth = t
                        hit_uv = hit
                        hit_pid = pid

                if hit_pid is None:
                    continue
                mask |= 1 << sample_index
                sample_depth[sample_index][py][px] = hit_depth
                sample_uvs[sample_index][py][px] = hit_uv
                tolerance = _DEPTH_REL_EPS * max(1.0, best_depth)
                if best_pid is None or hit_depth < best_depth - tolerance:
                    best_depth = hit_depth
                    best_uv = hit_uv
                    best_pid = hit_pid
                    best_sample = sample_index

            coverage[py][px] = mask
            if best_pid is not None:
                depth[py][px] = best_depth
                uv[py][px] = best_uv
                primitive_ids[py][px] = best_pid

    return ReferenceResult(
        width=width,
        height=height,
        samples=samples,
        coverage=coverage,
        depth=depth,
        uv=uv,
        primitive_ids=primitive_ids,
        sample_depths=sample_depth,
        sample_uvs=sample_uvs,
        near=near,
        far=far,
    )
