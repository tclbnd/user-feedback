"""Deterministic tile-bucketed software rasterizer.

Pipeline
--------
1.  Triangles are transformed into a homogeneous clip space with

    ``x_c = x_view / (tan(fov/2) * aspect)``
    ``y_c = y_view / tan(fov/2)``
    ``w_c = view_depth``

    The six frustum planes are the half-spaces

    ==================  ================================
    left                ``x_c + w_c >= 0``
    right               ``w_c - x_c >= 0``
    bottom              ``y_c + w_c >= 0``
    top                 ``w_c - y_c >= 0``
    near                ``w_c - near >= 0``
    far                 ``far - w_c >= 0``
    ==================  ================================

    Triangles are clipped (Sutherland-Hodgman) against each plane.  Every
    intersection interpolates all homogeneous coordinates *and* the UV
    attributes, so a triangle crossing the near plane is turned into a
    clipped polygon rather than dropped.

2.  Perspective division is applied exactly once, after clipping:
    ``x_ndc = x_c / w_c``, ``y_ndc = y_c / w_c``.  Depth is the view-space
    distance ``w_c``.  Interpolation uses the perspective-correct pair
    ``(1/w, attr/w)``: both are linear in screen barycentrics.

3.  Clipped screen triangles are bucketed into image tiles.  Tiles are
    rasterized independently and merged in a fixed row-major order, so the
    tile execution order and the number of workers cannot change the output.

4.  Every sample has its own depth/primitive buffer.  A fragment replaces the
    current one only when it is strictly nearer; bucket entries are processed
    in ascending primitive id, therefore an exact depth tie is won by the
    lower stable primitive id.  Shared triangle edges use a top-left fill rule
    so exactly one of two adjacent triangles owns a sample on the edge.
"""

from __future__ import annotations

import math
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from geometry import Pixel, Triangle, Vec2, Vec3, coerce_triangles

NEAR_PLANE = 1e-6
FAR_PLANE = 1e9
NEG_INF = float("-inf")

DEFAULT_BACKGROUND: Pixel = (16, 18, 24)
DEFAULT_BASE_COLOR: Pixel = (238, 140, 68)
DEFAULT_LIGHT_DIR: Tuple[float, float, float] = (0.45, 0.8, 1.0)
DEFAULT_AMBIENT = 0.18

# Sample positions inside a pixel.  Sample 0 is the pixel centre in 1x mode.
SAMPLE_OFFSETS_1: Tuple[Vec2, ...] = ((0.5, 0.5),)
SAMPLE_OFFSETS_4: Tuple[Vec2, ...] = (
    (0.25, 0.25),
    (0.75, 0.25),
    (0.25, 0.75),
    (0.75, 0.75),
)

_EDGE_REL_EPS = 1e-12
_VERTEX_REL_EPS = 1e-12
# Depths closer than this relative tolerance are treated as an exact tie.
# The lower stable primitive id (already processed first because buckets are
# sorted) wins such a tie.
_DEPTH_REL_EPS = 1e-12

# Clip planes as (a, b, c, d): distance = a*xc + b*yc + c*wc + d.
_PLANE_LEFT = (1.0, 0.0, 1.0, 0.0)
_PLANE_RIGHT = (-1.0, 0.0, 1.0, 0.0)
_PLANE_BOTTOM = (0.0, 1.0, 1.0, 0.0)
_PLANE_TOP = (0.0, -1.0, 1.0, 0.0)


@dataclass
class RenderResult:
    """Rendered RGB image plus deterministic auxiliary buffers."""

    width: int
    height: int
    pixels: List[List[Pixel]]
    triangle_count: int
    triangles_drawn: int
    foreground_pixels: int
    foreground_ratio: float  # fraction in [0, 1]
    depth_min: Optional[float]
    depth_max: Optional[float]
    coverage: List[List[int]]
    depth_values: List[List[Optional[float]]]
    uv_values: Optional[List[List[Optional[Vec2]]]]
    primitive_ids: List[List[Optional[int]]]
    samples: int
    tile_size: int
    near: float
    far: float


class Camera:
    """Perspective pinhole camera at ``(0, 0, distance)`` looking at origin."""

    def __init__(
        self,
        width: int,
        height: int,
        distance: float = 5.0,
        fov_deg: float = 45.0,
        near: float = NEAR_PLANE,
        far: float = FAR_PLANE,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("camera image dimensions must be positive")
        if distance <= 0.0:
            raise ValueError("camera distance must be positive")
        if not 0.0 < fov_deg < 180.0:
            raise ValueError("fov must be between 0 and 180 degrees")
        if near <= 0.0:
            raise ValueError("near plane must be positive")
        if far <= near:
            raise ValueError("far plane must be greater than near")

        self.width = int(width)
        self.height = int(height)
        self.distance = float(distance)
        self.fov_deg = float(fov_deg)
        self.near = float(near)
        self.far = float(far)
        self.eye: Vec3 = (0.0, 0.0, self.distance)
        self.forward: Vec3 = (0.0, 0.0, -1.0)
        self.right: Vec3 = (1.0, 0.0, 0.0)
        self.up: Vec3 = (0.0, 1.0, 0.0)
        self.tan_half_fov = math.tan(math.radians(self.fov_deg) * 0.5)
        self.aspect = float(width) / float(height)

    def clip_point(self, point: Vec3) -> Tuple[float, float, float]:
        """Return homogeneous clip coordinates ``(x_c, y_c, w_c)``."""
        dx = point[0] - self.eye[0]
        dy = point[1] - self.eye[1]
        w = self.eye[2] - point[2]  # view-space depth
        x_c = dx / (self.tan_half_fov * self.aspect)
        y_c = dy / self.tan_half_fov
        return (x_c, y_c, w)

    def project(self, point: Vec3):
        """Project a world point; return ``(sx, sy, depth, inv_depth)``.

        ``None`` is returned for points at or behind the near plane.
        """
        x_c, y_c, w = self.clip_point(point)
        if w <= self.near:
            return None
        ndc_x = x_c / w
        ndc_y = y_c / w
        sx = (ndc_x + 1.0) * 0.5 * self.width
        sy = (1.0 - ndc_y) * 0.5 * self.height
        return (sx, sy, w, 1.0 / w)


def _subtract(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(vector: Vec3) -> Optional[Vec3]:
    length = math.sqrt(_dot(vector, vector))
    # Only an exactly zero (or non-finite) vector is unusable.  Very small
    # but non-zero normals are still normalised so that sub-pixel triangles
    # are not discarded by an arbitrary magnitude threshold.
    if not math.isfinite(length) or length == 0.0:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _face_normal(triangle: Triangle) -> Optional[Vec3]:
    """Geometric normal from the triangle's winding (CCW is outward)."""
    return _normalize(_cross(_subtract(triangle.p1, triangle.p0),
                             _subtract(triangle.p2, triangle.p0)))


def _shade(normal: Vec3, light: Vec3, base: Pixel, ambient: float) -> Pixel:
    lambert = max(0.0, _dot(normal, light))
    intensity = ambient + (1.0 - ambient) * lambert
    return tuple(
        max(0, min(255, int(round(channel * intensity)))) for channel in base
    )  # type: ignore[return-value]


class _Prepared(NamedTuple):
    """A clipped, screen-space triangle ready for tile rasterization."""

    pid: int
    color: Pixel
    x0: float
    y0: float
    iw0: float
    x1: float
    y1: float
    iw1: float
    x2: float
    y2: float
    iw2: float
    area2: float
    eps: float
    tl0: bool
    tl1: bool
    tl2: bool
    xmin: int
    xmax: int
    ymin: int
    ymax: int
    has_uv: bool
    uo0: float
    vo0: float
    uo1: float
    vo1: float
    uo2: float
    vo2: float


# ---------------------------------------------------------------------------
# Homogeneous clipping


def _plane_distance(plane: Tuple[float, float, float, float],
                    vertex: Sequence[float]) -> float:
    a, b, c, d = plane
    return a * vertex[0] + b * vertex[1] + c * vertex[2] + d


def _clip_polygon(vertices: List[List[float]],
                  plane: Tuple[float, float, float, float]) -> List[List[float]]:
    """Clip a convex polygon (list of ``[xc, yc, wc, u, v]``) by *plane*."""
    a, b, c, d = plane
    output: List[List[float]] = []
    count = len(vertices)
    for index in range(count):
        current = vertices[index]
        following = vertices[(index + 1) % count]

        dc = a * current[0] + b * current[1] + c * current[2] + d
        dn = a * following[0] + b * following[1] + c * following[2] + d
        current_inside = dc >= 0.0
        following_inside = dn >= 0.0

        if current_inside:
            output.append(current)

        if current_inside != following_inside:
            denominator = dc - dn
            if denominator != 0.0:
                t = dc / denominator
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                output.append([
                    current[0] + t * (following[0] - current[0]),
                    current[1] + t * (following[1] - current[1]),
                    current[2] + t * (following[2] - current[2]),
                    current[3] + t * (following[3] - current[3]),
                    current[4] + t * (following[4] - current[4]),
                ])
    return output


def _dedupe_polygon(vertices: List[List[float]]) -> List[List[float]]:
    if len(vertices) < 2:
        return vertices
    result: List[List[float]] = []
    for vertex in vertices:
        if result and _close_clip(vertex, result[-1]):
            continue
        result.append(vertex)
    if len(result) > 1 and _close_clip(result[0], result[-1]):
        result.pop()
    return result


def _close_clip(a: Sequence[float], b: Sequence[float]) -> bool:
    scale = max(1.0, abs(a[0]), abs(a[1]), abs(a[2]),
                abs(b[0]), abs(b[1]), abs(b[2]))
    tolerance = _VERTEX_REL_EPS * scale
    return (
        abs(a[0] - b[0]) <= tolerance
        and abs(a[1] - b[1]) <= tolerance
        and abs(a[2] - b[2]) <= tolerance
    )


def _clip_to_frustum(
    vertices: List[List[float]],
    near: float,
    far: float,
) -> List[List[float]]:
    """Clip ``[xc, yc, wc, u, v]`` vertices against all six planes."""
    plane_near = (0.0, 0.0, 1.0, -near)
    plane_far = (0.0, 0.0, -1.0, far)
    for plane in (_PLANE_LEFT, _PLANE_RIGHT, _PLANE_BOTTOM, _PLANE_TOP,
                  plane_near, plane_far):
        if not vertices:
            return []
        # Fast path: keep the polygon untouched when it is entirely inside.
        if all(_plane_distance(plane, vertex) >= 0.0 for vertex in vertices):
            continue
        vertices = _clip_polygon(vertices, plane)
    return _dedupe_polygon(vertices)


# ---------------------------------------------------------------------------
# Triangle preparation


def _prepare_triangle(
    triangle: Triangle,
    pid: int,
    camera: Camera,
    cull_backfaces: bool,
    light: Vec3,
    base_color: Pixel,
    ambient: float,
) -> Optional[List[_Prepared]]:
    """Clip, project and normalise one input triangle.

    Returns a list of screen-space triangles (a clipped triangle may become
    several), or ``None`` when the primitive cannot contribute.
    """
    normal = _face_normal(triangle)
    if normal is None:
        return None  # exactly degenerate in object space

    if cull_backfaces:
        centroid = (
            (triangle.p0[0] + triangle.p1[0] + triangle.p2[0]) / 3.0,
            (triangle.p0[1] + triangle.p1[1] + triangle.p2[1]) / 3.0,
            (triangle.p0[2] + triangle.p1[2] + triangle.p2[2]) / 3.0,
        )
        if _dot(normal, _subtract(camera.eye, centroid)) <= 0.0:
            return None

    color = triangle.color if triangle.color is not None else base_color
    shaded = _shade(normal, light, color, ambient)
    has_uv = (
        triangle.uv0 is not None
        and triangle.uv1 is not None
        and triangle.uv2 is not None
    )

    clip_vertices: List[List[float]] = []
    for position, uv in (
        (triangle.p0, triangle.uv0 if has_uv else (0.0, 0.0)),
        (triangle.p1, triangle.uv1 if has_uv else (0.0, 0.0)),
        (triangle.p2, triangle.uv2 if has_uv else (0.0, 0.0)),
    ):
        x_c, y_c, w = camera.clip_point(position)
        clip_vertices.append([x_c, y_c, w, uv[0], uv[1]])

    polygon = _clip_to_frustum(clip_vertices, camera.near, camera.far)
    if len(polygon) < 3:
        return None

    prepared: List[_Prepared] = []
    for index in range(1, len(polygon) - 1):
        prepared_triangle = _project_polygon_triangle(
            polygon[0], polygon[index], polygon[index + 1],
            pid, shaded, has_uv, camera.width, camera.height,
        )
        if prepared_triangle is not None:
            prepared.append(prepared_triangle)
    return prepared if prepared else None


def _project_polygon_triangle(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    pid: int,
    color: Pixel,
    has_uv: bool,
    width: int,
    height: int,
) -> Optional[_Prepared]:
    wa, wb, wc = a[2], b[2], c[2]
    if not (math.isfinite(wa) and math.isfinite(wb) and math.isfinite(wc)):
        return None
    if wa <= 0.0 or wb <= 0.0 or wc <= 0.0:
        return None

    iw_a = 1.0 / wa
    iw_b = 1.0 / wb
    iw_c = 1.0 / wc
    if not (math.isfinite(iw_a) and math.isfinite(iw_b) and math.isfinite(iw_c)):
        return None

    # Perspective division happens here, once, after clipping.
    ndc_ax = a[0] / wa
    ndc_ay = a[1] / wa
    ndc_bx = b[0] / wb
    ndc_by = b[1] / wb
    ndc_cx = c[0] / wc
    ndc_cy = c[1] / wc

    if not all(math.isfinite(value) for value in (
        ndc_ax, ndc_ay, ndc_bx, ndc_by, ndc_cx, ndc_cy,
    )):
        return None

    x0 = (ndc_ax + 1.0) * 0.5 * width
    y0 = (1.0 - ndc_ay) * 0.5 * height
    x1 = (ndc_bx + 1.0) * 0.5 * width
    y1 = (1.0 - ndc_by) * 0.5 * height
    x2 = (ndc_cx + 1.0) * 0.5 * width
    y2 = (1.0 - ndc_cy) * 0.5 * height

    if not all(math.isfinite(value) for value in (x0, y0, x1, y1, x2, y2)):
        return None

    area2 = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    if area2 < 0.0:
        # Normalise winding so the top-left rule is unambiguous.  Swapping
        # the two non-anchor vertices preserves the covered region, depth
        # and attributes exactly.
        x1, y1, x2, y2 = x2, y2, x1, y1
        iw_b, iw_c = iw_c, iw_b
        b, c = c, b
        area2 = -area2
    if area2 <= 0.0 or not math.isfinite(area2):
        return None  # exactly degenerate or non-representable
    if area2 < 1e-300:
        return None  # numerically hopeless in reciprocal form

    # Edge-function boundary tolerance.  It scales with the coordinate
    # magnitude (rounded products are O(coord^2)) but is capped relative to
    # the triangle's area, so a genuinely tiny triangle still gets a
    # meaningful interior rather than being classified entirely as boundary.
    coordinate_scale = max(
        1.0, abs(x0), abs(x1), abs(x2), abs(y0), abs(y1), abs(y2)
    )
    eps = min(
        _EDGE_REL_EPS * coordinate_scale * coordinate_scale,
        1e-6 * area2,
    )

    # Top-left ownership for the three directed edges p0->p1, p1->p2,
    # p2->p0.  e0 is the edge p1->p2, e1 is p2->p0, e2 is p0->p1.
    tl0 = _is_top_left(x2 - x1, y2 - y1)
    tl1 = _is_top_left(x0 - x2, y0 - y2)
    tl2 = _is_top_left(x1 - x0, y1 - y0)

    if has_uv:
        uo0, vo0 = a[3] * iw_a, a[4] * iw_a
        uo1, vo1 = b[3] * iw_b, b[4] * iw_b
        uo2, vo2 = c[3] * iw_c, c[4] * iw_c
    else:
        uo0 = vo0 = uo1 = vo1 = uo2 = vo2 = 0.0

    xmin = max(0, int(math.floor(min(x0, x1, x2))))
    xmax = min(width - 1, int(math.ceil(max(x0, x1, x2))))
    ymin = max(0, int(math.floor(min(y0, y1, y2))))
    ymax = min(height - 1, int(math.ceil(max(y0, y1, y2))))

    return _Prepared(
        pid, color,
        x0, y0, iw_a,
        x1, y1, iw_b,
        x2, y2, iw_c,
        area2, eps, tl0, tl1, tl2,
        xmin, xmax, ymin, ymax,
        has_uv, uo0, vo0, uo1, vo1, uo2, vo2,
    )


def _is_top_left(dx: float, dy: float) -> bool:
    # Screen y grows downwards, so a "left" edge runs down (dy > 0) and a
    # "top" edge runs right (dy == 0 and dx > 0).  Reversing an edge flips
    # exactly one of these tests, so a shared edge has exactly one owner.
    return dy > 0.0 or (dy == 0.0 and dx > 0.0)


# ---------------------------------------------------------------------------
# Tile jobs


class _JobContext:
    __slots__ = (
        "prepared", "sort_keys", "buckets", "tiles_x", "tiles_y",
        "tile_size", "width", "height", "samples", "offsets", "collect_uv",
        "background", "near", "far",
    )

    def __init__(self) -> None:
        # Dense list of clipped screen triangles.  ``sort_keys`` holds the
        # stable (original primitive id, sub-triangle index) key used to
        # order fragments with exactly equal depth.
        self.prepared: List[_Prepared] = []
        self.sort_keys: List[Tuple[int, int]] = []
        self.buckets: List[List[int]] = []
        self.tiles_x = 0
        self.tiles_y = 0
        self.tile_size = 0
        self.width = 0
        self.height = 0
        self.samples = 1
        self.offsets: Tuple[Vec2, ...] = SAMPLE_OFFSETS_1
        self.collect_uv = False
        self.background: Pixel = DEFAULT_BACKGROUND
        self.near = NEAR_PLANE
        self.far = FAR_PLANE


_JOB: Optional[_JobContext] = None


def _render_tile(tile_index: int):
    """Render one tile and return its local buffers.

    Reads the module-level :data:`_JOB` context.  This is deliberately a
    top-level function so it can be used by both process and thread pools.
    """
    context = _JOB
    if context is None:
        raise RuntimeError("tile job context is not initialised")

    tile_size = context.tile_size
    tiles_x = context.tiles_x
    tx = tile_index % tiles_x
    ty = tile_index // tiles_x
    x_start = tx * tile_size
    y_start = ty * tile_size
    width = min(context.width, x_start + tile_size) - x_start
    height = min(context.height, y_start + tile_size) - y_start
    pixel_count = width * height

    samples = context.samples
    offsets = context.offsets
    depth_samples = [[NEG_INF] * pixel_count for _ in range(samples)]
    pid_samples = [[-1] * pixel_count for _ in range(samples)]
    uv_samples = (
        [[None] * pixel_count for _ in range(samples)]
        if context.collect_uv else None
    )

    prepared = context.prepared
    sort_keys = context.sort_keys
    bucket = context.buckets[tile_index]

    for dense_index in sorted(bucket, key=sort_keys.__getitem__):
        primitive = prepared[dense_index]
        (
            _pid, color,
            x0, y0, iw0,
            x1, y1, iw1,
            x2, y2, iw2,
            area2, eps, tl0, tl1, tl2,
            xmin, xmax, ymin, ymax,
            has_uv, uo0, vo0, uo1, vo1, uo2, vo2,
        ) = primitive

        px_start = max(x_start, xmin)
        px_end = min(x_start + width - 1, xmax)
        py_start = max(y_start, ymin)
        py_end = min(y_start + height - 1, ymax)
        if px_end < px_start or py_end < py_start:
            continue

        for py in range(py_start, py_end + 1):
            row_offset = (py - y_start) * width
            for px in range(px_start, px_end + 1):
                local = row_offset + (px - x_start)
                for sample_index in range(samples):
                    offset_x, offset_y = offsets[sample_index]
                    sample_x = px + offset_x
                    sample_y = py + offset_y

                    # Edge functions.  e0 belongs to the edge p1->p2,
                    # e1 to p2->p0, e2 to p0->p1.
                    e0 = ((x1 - sample_x) * (y2 - sample_y)
                          - (y1 - sample_y) * (x2 - sample_x))
                    e1 = ((x2 - sample_x) * (y0 - sample_y)
                          - (y2 - sample_y) * (x0 - sample_x))
                    e2 = ((x0 - sample_x) * (y1 - sample_y)
                          - (y0 - sample_y) * (x1 - sample_x))

                    if e0 < -eps or (e0 <= eps and not tl0):
                        continue
                    if e1 < -eps or (e1 <= eps and not tl1):
                        continue
                    if e2 < -eps or (e2 <= eps and not tl2):
                        continue

                    w0 = e0 / area2
                    w1 = e1 / area2
                    w2 = 1.0 - w0 - w1
                    inv_depth = w0 * iw0 + w1 * iw1 + w2 * iw2
                    if not math.isfinite(inv_depth) or inv_depth <= 0.0:
                        continue

                    current_depth = depth_samples[sample_index][local]
                    tied = (
                        current_depth != NEG_INF
                        and abs(inv_depth - current_depth)
                        <= _DEPTH_REL_EPS * max(1.0, abs(current_depth))
                    )
                    if current_depth == NEG_INF or (inv_depth > current_depth
                                                    and not tied):
                        depth_samples[sample_index][local] = inv_depth
                        pid_samples[sample_index][local] = dense_index
                        if uv_samples is not None and has_uv:
                            u = (w0 * uo0 + w1 * uo1 + w2 * uo2) / inv_depth
                            v = (w0 * vo0 + w1 * vo1 + w2 * vo2) / inv_depth
                            uv_samples[sample_index][local] = (u, v)

    color_rows: List[List[Pixel]] = []
    depth_rows: List[List[Optional[float]]] = []
    coverage_rows: List[List[int]] = []
    uv_rows: Optional[List[List[Optional[Vec2]]]] = [] if uv_samples is not None else None
    pid_rows: List[List[Optional[int]]] = []

    background = context.background
    near = context.near
    far = context.far

    for row in range(height):
        color_row: List[Pixel] = []
        depth_row: List[Optional[float]] = []
        coverage_row: List[int] = []
        uv_row: List[Optional[Vec2]] = [] if uv_rows is not None else []
        pid_row: List[Optional[int]] = []

        base = row * width
        for column in range(width):
            local = base + column
            mask = 0
            covered_count = 0
            red = green = blue = 0
            best_depth = NEG_INF
            best_dense = -1
            best_sample = -1

            for sample_index in range(samples):
                inv_depth = depth_samples[sample_index][local]
                if inv_depth == NEG_INF:
                    continue
                dense_index = pid_samples[sample_index][local]
                mask |= 1 << sample_index
                covered_count += 1
                sample_color = prepared[dense_index].color
                red += sample_color[0]
                green += sample_color[1]
                blue += sample_color[2]

                tied = (
                    best_depth != NEG_INF
                    and abs(inv_depth - best_depth)
                    <= _DEPTH_REL_EPS * max(1.0, abs(best_depth))
                )
                if best_depth == NEG_INF or (inv_depth > best_depth and not tied):
                    best_depth = inv_depth
                    best_dense = dense_index
                    best_sample = sample_index

            if covered_count == 0:
                color_row.append(background)
                depth_row.append(None)
                coverage_row.append(0)
                if uv_rows is not None:
                    uv_row.append(None)
                pid_row.append(None)
                continue

            half = covered_count // 2
            color_row.append((
                (red + half) // covered_count,
                (green + half) // covered_count,
                (blue + half) // covered_count,
            ))
            coverage_row.append(mask)
            pid_row.append(prepared[best_dense].pid)

            depth = 1.0 / best_depth
            if not math.isfinite(depth) or depth <= 0.0:
                # Should be impossible: all stored inv depths are finite and
                # positive.  Treat it as uncovered rather than emitting a
                # non-finite depth into the dataset.
                depth_row.append(None)
                coverage_row[-1] = 0
                pid_row[-1] = None
                if uv_rows is not None:
                    uv_row.append(None)
                continue
            if depth < near:
                depth = near
            elif depth > far:
                depth = far
            depth_row.append(depth)
            if uv_rows is not None:
                uv_row.append(uv_samples[best_sample][local])

        color_rows.append(color_row)
        depth_rows.append(depth_row)
        coverage_rows.append(coverage_row)
        if uv_rows is not None:
            uv_rows.append(uv_row)
        pid_rows.append(pid_row)

    return (color_rows, depth_rows, coverage_rows, uv_rows, pid_rows)


def _render_tile_worker(tile_index: int):
    return tile_index, _render_tile(tile_index)


def _tile_order(tiles_x: int, tiles_y: int, order: str) -> List[int]:
    indices = [ty * tiles_x + tx for ty in range(tiles_y) for tx in range(tiles_x)]
    if order == "row":
        return indices
    if order == "reverse":
        return list(reversed(indices))
    if order == "column":
        return [ty * tiles_x + tx for tx in range(tiles_x) for ty in range(tiles_y)]
    if order == "serpentine":
        result: List[int] = []
        for ty in range(tiles_y):
            row = [ty * tiles_x + tx for tx in range(tiles_x)]
            if ty % 2:
                row.reverse()
            result.extend(row)
        return result
    if order == "shuffle":
        result = list(indices)
        random.Random(0).shuffle(result)
        return result
    raise ValueError("tile_order must be 'row', 'reverse', 'serpentine', "
                     "'column' or 'shuffle'")


def _run_tiles(
    context: _JobContext,
    tiles: List[int],
    workers: int,
) -> Dict[int, object]:
    global _JOB
    _JOB = context
    results: Dict[int, object] = {}
    try:
        if workers == 1:
            for tile_index in tiles:
                results[tile_index] = _render_tile(tile_index)
            return results

        # Prefer real worker processes.  ``fork`` lets the children inherit
        # the prepared primitives without serialising the whole scene.
        used_processes = False
        try:
            import multiprocessing as multiprocessing

            if "fork" in multiprocessing.get_all_start_methods():
                multiprocessing_context = multiprocessing.get_context("fork")
                chunk = max(1, len(tiles) // (workers * 4))
                with multiprocessing_context.Pool(processes=workers) as pool:
                    for tile_index, tile in pool.imap_unordered(
                        _render_tile_worker, tiles, chunksize=chunk
                    ):
                        results[tile_index] = tile
                used_processes = True
        except OSError:
            used_processes = False

        if not used_processes:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for tile_index, tile in executor.map(_render_tile_worker, tiles):
                    results[tile_index] = tile
        return results
    finally:
        _JOB = None


# ---------------------------------------------------------------------------
# Public entry point


def render(
    triangles: Sequence,
    size: int = 512,
    fov_deg: float = 45.0,
    distance: float = 5.0,
    cull_backfaces: bool = True,
    light_dir: Sequence[float] = DEFAULT_LIGHT_DIR,
    background: Pixel = DEFAULT_BACKGROUND,
    base_color: Pixel = DEFAULT_BASE_COLOR,
    ambient: float = DEFAULT_AMBIENT,
    samples: int = 1,
    tile_size: int = 32,
    workers: int = 1,
    tile_order: str = "row",
    near: float = NEAR_PLANE,
    far: float = FAR_PLANE,
    collect_uv: bool = True,
) -> RenderResult:
    """Render *triangles* deterministically to a square RGB image.

    ``samples`` is 1 (pixel centre) or 4 (four fixed sub-pixel positions).
    ``tile_size`` and ``workers`` only affect execution, never output.
    ``tile_order`` selects a deterministic traversal of the tile grid.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if samples not in (1, 4):
        raise ValueError("samples must be 1 or 4")
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if workers <= 0:
        raise ValueError("workers must be positive")
    if not 0.0 <= ambient <= 1.0:
        raise ValueError("ambient must be between 0 and 1")
    if near <= 0.0:
        raise ValueError("near must be positive")
    if far <= near:
        raise ValueError("far must be greater than near")

    light = _normalize(
        (float(light_dir[0]), float(light_dir[1]), float(light_dir[2]))
    )
    if light is None:
        raise ValueError("light direction must be non-zero")

    scene = coerce_triangles(triangles)
    width = height = int(size)
    camera = Camera(width, height, distance=distance, fov_deg=fov_deg,
                    near=near, far=far)
    offsets = SAMPLE_OFFSETS_1 if samples == 1 else SAMPLE_OFFSETS_4

    context = _JobContext()
    triangles_drawn = 0
    for pid, triangle in enumerate(scene):
        clipped = _prepare_triangle(
            triangle, pid, camera, cull_backfaces, light, base_color, ambient
        )
        if clipped is None:
            continue
        triangles_drawn += 1
        # A clipped primitive can produce several screen triangles.  Their
        # stable ordering key is (original primitive id, sub-triangle index),
        # so fragments from the same source triangle keep a defined order.
        for sub_index, primitive in enumerate(clipped):
            context.prepared.append(primitive)
            context.sort_keys.append((pid, sub_index))

    tiles_x = (width + tile_size - 1) // tile_size
    tiles_y = (height + tile_size - 1) // tile_size
    context.tiles_x = tiles_x
    context.tiles_y = tiles_y
    context.tile_size = tile_size
    context.width = width
    context.height = height
    context.samples = samples
    context.offsets = offsets
    context.collect_uv = bool(collect_uv)
    context.background = background
    context.near = near
    context.far = far
    context.buckets = [[] for _ in range(tiles_x * tiles_y)]

    for dense_index, primitive in enumerate(context.prepared):
        if (primitive.xmax < primitive.xmin
                or primitive.ymax < primitive.ymin):
            continue
        tile_x_start = primitive.xmin // tile_size
        tile_x_end = primitive.xmax // tile_size
        tile_y_start = primitive.ymin // tile_size
        tile_y_end = primitive.ymax // tile_size
        for tile_y in range(tile_y_start, tile_y_end + 1):
            base = tile_y * tiles_x
            for tile_x in range(tile_x_start, tile_x_end + 1):
                context.buckets[base + tile_x].append(dense_index)

    order = _tile_order(tiles_x, tiles_y, tile_order)
    tile_results = _run_tiles(context, order, workers)

    color_buffer: List[List[Pixel]] = [
        [background] * width for _ in range(height)
    ]
    depth_buffer: List[List[Optional[float]]] = [
        [None] * width for _ in range(height)
    ]
    coverage_buffer: List[List[int]] = [
        [0] * width for _ in range(height)
    ]
    uv_buffer: Optional[List[List[Optional[Vec2]]]] = (
        [[None] * width for _ in range(height)] if collect_uv else None
    )
    pid_buffer: List[List[Optional[int]]] = [
        [None] * width for _ in range(height)
    ]

    for tile_y in range(tiles_y):
        for tile_x in range(tiles_x):
            tile_index = tile_y * tiles_x + tile_x
            color_rows, depth_rows, coverage_rows, uv_rows, pid_rows = (
                tile_results[tile_index]  # type: ignore[misc]
            )
            x_start = tile_x * tile_size
            y_start = tile_y * tile_size
            tile_width = min(width, x_start + tile_size) - x_start
            for row in range(len(color_rows)):
                destination = y_start + row
                color_buffer[destination][x_start:x_start + tile_width] = (
                    color_rows[row]
                )
                depth_buffer[destination][x_start:x_start + tile_width] = (
                    depth_rows[row]
                )
                coverage_buffer[destination][x_start:x_start + tile_width] = (
                    coverage_rows[row]
                )
                pid_buffer[destination][x_start:x_start + tile_width] = (
                    pid_rows[row]
                )
                if uv_buffer is not None and uv_rows is not None:
                    uv_buffer[destination][x_start:x_start + tile_width] = (
                        uv_rows[row]
                    )

    foreground = 0
    depth_min = math.inf
    depth_max = -math.inf
    for row in depth_buffer:
        for depth in row:
            if depth is None:
                continue
            if not math.isfinite(depth):
                continue
            foreground += 1
            if depth < depth_min:
                depth_min = depth
            if depth > depth_max:
                depth_max = depth

    total_pixels = width * height
    return RenderResult(
        width=width,
        height=height,
        pixels=color_buffer,
        triangle_count=len(scene),
        triangles_drawn=triangles_drawn,
        foreground_pixels=foreground,
        foreground_ratio=foreground / float(total_pixels),
        depth_min=depth_min if foreground else None,
        depth_max=depth_max if foreground else None,
        coverage=coverage_buffer,
        depth_values=depth_buffer,
        uv_values=uv_buffer,
        primitive_ids=pid_buffer,
        samples=samples,
        tile_size=tile_size,
        near=near,
        far=far,
    )
