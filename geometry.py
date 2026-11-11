"""Shared geometry types for the renderer and the reference path.

The renderer accepts two triangle representations:

* legacy ``(p0, p1, p2)`` tuples where each ``p`` is an ``(x, y, z)`` tuple
  (the form returned by :func:`obj_loader.load_obj`);
* :class:`Triangle` records which additionally carry per-corner texture
  coordinates and an optional flat colour.

Both are normalised by :func:`coerce_triangles` before rasterization.
``primitive_id`` is always the triangle's index in the input sequence, which
makes the input order the stable tie-breaker for equal-depth fragments.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Sequence, Tuple, Union

Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Pixel = Tuple[int, int, int]


class Triangle(NamedTuple):
    """A triangle with optional per-corner UVs and a flat colour."""

    p0: Vec3
    p1: Vec3
    p2: Vec3
    uv0: Optional[Vec2] = None
    uv1: Optional[Vec2] = None
    uv2: Optional[Vec2] = None
    color: Optional[Pixel] = None


LegacyTriangle = Tuple[Vec3, Vec3, Vec3]
TriangleLike = Union[Triangle, LegacyTriangle]


def coerce_triangles(
    triangles: Sequence[TriangleLike],
) -> List[Triangle]:
    """Return *triangles* as :class:`Triangle` records.

    Legacy three-point tuples are wrapped with ``uv=None`` and ``color=None``.
    """
    result: List[Triangle] = []
    for triangle in triangles:
        if isinstance(triangle, Triangle):
            result.append(triangle)
            continue
        if len(triangle) != 3:
            raise ValueError("a triangle needs exactly three vertices")
        p0, p1, p2 = triangle
        if not (
            _is_vec3(p0) and _is_vec3(p1) and _is_vec3(p2)
        ):
            raise ValueError("triangle vertices must be (x, y, z) triples")
        result.append(Triangle(p0, p1, p2))
    return result


def _is_vec3(value) -> bool:
    try:
        return len(value) == 3
    except TypeError:
        return False
