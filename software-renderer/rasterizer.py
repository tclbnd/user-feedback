"""Software rasterizer: perspective camera, z-buffer, Lambert shading.

Camera model
------------
The camera sits on the +Z axis at ``(0, 0, distance)`` and looks at the
origin (view direction ``-Z``).  It has a configurable vertical field of
view.  Vertices are projected to screen space with a standard perspective
divide; the reciprocal of view-space depth is interpolated linearly in
screen space, which is exact for a plane under perspective.

Rasterization
-------------
Triangles are scan-converted with screen-space barycentric coordinates
(edge functions) at pixel centres.  A z-buffer keeps the nearest fragment
per pixel.  Face normals are computed from the OBJ winding and optionally
used for backface culling.  A directional light with ambient plus Lambert
diffuse shading colours each visible face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Vertex = Tuple[float, float, float]
Triangle = Tuple[Vertex, Vertex, Vertex]
Pixel = Tuple[int, int, int]

NEAR_PLANE = 1e-6
NEG_INF = float("-inf")

DEFAULT_BACKGROUND: Pixel = (16, 18, 24)
DEFAULT_BASE_COLOR: Pixel = (238, 140, 68)
DEFAULT_LIGHT_DIR: Tuple[float, float, float] = (0.45, 0.8, 1.0)
DEFAULT_AMBIENT = 0.18


@dataclass
class RenderResult:
    """Rendered RGB image plus the statistics required by the CLI."""

    width: int
    height: int
    pixels: List[List[Pixel]]
    triangle_count: int
    triangles_drawn: int
    foreground_pixels: int
    foreground_ratio: float  # fraction in [0, 1]
    depth_min: Optional[float]
    depth_max: Optional[float]


class Camera:
    """Perspective pinhole camera at ``(0, 0, distance)`` looking at origin."""

    def __init__(
        self,
        width: int,
        height: int,
        distance: float = 5.0,
        fov_deg: float = 45.0,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("camera image dimensions must be positive")
        if distance <= 0.0:
            raise ValueError("camera distance must be positive")
        if not 0.0 < fov_deg < 180.0:
            raise ValueError("fov must be between 0 and 180 degrees")

        self.width = width
        self.height = height
        self.distance = float(distance)
        self.fov_deg = float(fov_deg)
        self.eye: Vertex = (0.0, 0.0, self.distance)
        self.forward: Vertex = (0.0, 0.0, -1.0)
        self.right: Vertex = (1.0, 0.0, 0.0)
        self.up: Vertex = (0.0, 1.0, 0.0)
        self.tan_half_fov = math.tan(math.radians(self.fov_deg) * 0.5)
        self.aspect = float(width) / float(height)

    def project(self, point: Vertex):
        """Project a world point.

        Returns ``(screen_x, screen_y, view_depth, inv_depth)`` or ``None``
        when the point is at or behind the near plane.
        """
        dx = point[0] - self.eye[0]
        dy = point[1] - self.eye[1]
        # forward is (0, 0, -1), so view depth is -(z - eye_z).
        view_depth = -(point[2] - self.eye[2])
        if view_depth <= NEAR_PLANE:
            return None

        ndc_x = dx / (view_depth * self.tan_half_fov * self.aspect)
        ndc_y = dy / (view_depth * self.tan_half_fov)
        screen_x = (ndc_x + 1.0) * 0.5 * self.width
        screen_y = (1.0 - ndc_y) * 0.5 * self.height
        return (screen_x, screen_y, view_depth, 1.0 / view_depth)


def _subtract(a: Vertex, b: Vertex) -> Vertex:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vertex, b: Vertex) -> Vertex:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(vector: Vertex) -> Optional[Vertex]:
    length = math.sqrt(_dot(vector, vector))
    if length <= 1e-12:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _face_normal(triangle: Triangle) -> Optional[Vertex]:
    """Geometric normal from the triangle's winding (CCW is outward)."""
    a, b, c = triangle
    return _normalize(_cross(_subtract(b, a), _subtract(c, a)))


def _shade(normal: Vertex, light: Vertex, base: Pixel, ambient: float) -> Pixel:
    lambert = max(0.0, _dot(normal, light))
    intensity = ambient + (1.0 - ambient) * lambert
    return tuple(
        max(0, min(255, int(round(channel * intensity)))) for channel in base
    )  # type: ignore[return-value]


def render(
    triangles: Sequence[Triangle],
    size: int = 512,
    fov_deg: float = 45.0,
    distance: float = 5.0,
    cull_backfaces: bool = True,
    light_dir: Sequence[float] = DEFAULT_LIGHT_DIR,
    background: Pixel = DEFAULT_BACKGROUND,
    base_color: Pixel = DEFAULT_BASE_COLOR,
    ambient: float = DEFAULT_AMBIENT,
) -> RenderResult:
    """Render *triangles* to a square RGB image.

    ``light_dir`` points *towards* the directional light in world space.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0.0 <= ambient <= 1.0:
        raise ValueError("ambient must be between 0 and 1")

    light = _normalize(
        (float(light_dir[0]), float(light_dir[1]), float(light_dir[2]))
    )
    if light is None:
        raise ValueError("light direction must be non-zero")

    width = height = int(size)
    camera = Camera(width, height, distance=distance, fov_deg=fov_deg)

    # -inf means "no fragment yet" for the reciprocal-depth buffer.
    color_buffer: List[List[Pixel]] = [
        [background] * width for _ in range(height)
    ]
    depth_buffer: List[List[float]] = [
        [NEG_INF] * width for _ in range(height)
    ]

    triangles_drawn = 0

    for triangle in triangles:
        normal = _face_normal(triangle)
        if normal is None:
            continue  # degenerate triangle

        if cull_backfaces:
            centroid = (
                (triangle[0][0] + triangle[1][0] + triangle[2][0]) / 3.0,
                (triangle[0][1] + triangle[1][1] + triangle[2][1]) / 3.0,
                (triangle[0][2] + triangle[1][2] + triangle[2][2]) / 3.0,
            )
            if _dot(normal, _subtract(camera.eye, centroid)) <= 0.0:
                continue

        projected = [camera.project(vertex) for vertex in triangle]
        if any(point is None for point in projected):
            continue  # no near-plane clipping; skip crossing triangles

        x0, y0, _, inv0 = projected[0]  # type: ignore[misc]
        x1, y1, _, inv1 = projected[1]  # type: ignore[misc]
        x2, y2, _, inv2 = projected[2]  # type: ignore[misc]

        area2 = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
        if abs(area2) <= 1e-12:
            continue  # edge-on or degenerate after projection

        triangles_drawn += 1
        shaded = _shade(normal, light, base_color, ambient)

        # Pixel-centre bounding box, clipped to the image.
        x_min = max(0, int(math.floor(min(x0, x1, x2))))
        x_max = min(width - 1, int(math.ceil(max(x0, x1, x2))))
        y_min = max(0, int(math.floor(min(y0, y1, y2))))
        y_max = min(height - 1, int(math.ceil(max(y0, y1, y2))))
        if x_max < x_min or y_max < y_min:
            continue

        for py in range(y_min, y_max + 1):
            sample_y = py + 0.5
            depth_row = depth_buffer[py]
            color_row = color_buffer[py]
            for px in range(x_min, x_max + 1):
                sample_x = px + 0.5

                # Barycentric weights at the sample point.
                w0 = (
                    (x1 - sample_x) * (y2 - sample_y)
                    - (y1 - sample_y) * (x2 - sample_x)
                ) / area2
                w1 = (
                    (x2 - sample_x) * (y0 - sample_y)
                    - (y2 - sample_y) * (x0 - sample_x)
                ) / area2
                w2 = 1.0 - w0 - w1
                if w0 < -1e-9 or w1 < -1e-9 or w2 < -1e-9:
                    continue

                # 1/z varies linearly across a projected triangle.
                inv_depth = w0 * inv0 + w1 * inv1 + w2 * inv2
                if inv_depth > depth_row[px]:
                    depth_row[px] = inv_depth
                    color_row[px] = shaded

    # Collect statistics from the resolved buffers.
    foreground = 0
    depth_min = math.inf
    depth_max = -math.inf
    for py in range(height):
        depth_row = depth_buffer[py]
        for px in range(width):
            inv_depth = depth_row[px]
            if inv_depth == NEG_INF:
                continue
            foreground += 1
            depth = 1.0 / inv_depth
            if depth < depth_min:
                depth_min = depth
            if depth > depth_max:
                depth_max = depth

    total_pixels = width * height
    return RenderResult(
        width=width,
        height=height,
        pixels=color_buffer,
        triangle_count=len(triangles),
        triangles_drawn=triangles_drawn,
        foreground_pixels=foreground,
        foreground_ratio=foreground / float(total_pixels),
        depth_min=depth_min if foreground else None,
        depth_max=depth_max if foreground else None,
    )
