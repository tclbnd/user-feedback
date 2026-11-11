"""Deterministic test/benchmark scenes.

Every scene is generated from plain Python floats in a fixed order, so the
same interpreter build produces byte-identical geometry on every run.  The
scenes deliberately exercise the parts of the renderer that the base cube
does not: near-plane crossings, frustum-boundary shared edges, coplanar
triangulations, overlapping surfaces, mixed winding and large meshes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from geometry import Pixel, Triangle, Vec2, Vec3

ORANGE: Pixel = (238, 140, 68)
BLUE: Pixel = (70, 140, 220)
GREEN: Pixel = (90, 200, 130)
GREY: Pixel = (150, 150, 150)


@dataclass
class Scene:
    name: str
    triangles: List[Triangle]
    cull_backfaces: bool = True
    description: str = ""
    tags: Tuple[str, ...] = field(default_factory=tuple)


def _quad(
    p0: Vec3,
    p1: Vec3,
    p2: Vec3,
    p3: Vec3,
    uvs: Optional[Tuple[Vec2, Vec2, Vec2, Vec2]] = None,
    color: Optional[Pixel] = None,
    diagonal: str = "02",
) -> List[Triangle]:
    """Turn a convex quad into two triangles with the requested diagonal.

    ``diagonal="02"`` uses the 0-2 diagonal (fan triangulation); ``"13"``
    uses the alternate 1-3 diagonal.  Both cover exactly the same region.
    """
    if uvs is None:
        uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    if diagonal == "02":
        pairs = ((0, 1, 2), (0, 2, 3))
    elif diagonal == "13":
        pairs = ((0, 1, 3), (1, 2, 3))
    else:
        raise ValueError("diagonal must be '02' or '13'")
    points = (p0, p1, p2, p3)
    return [
        Triangle(
            points[a], points[b], points[c],
            uvs[a], uvs[b], uvs[c],
            color,
        )
        for a, b, c in pairs
    ]


def coplanar_quad(diagonal: str = "02", color: Optional[Pixel] = None) -> Scene:
    """A flat square at z=1 (view depth 4), facing the camera."""
    triangles = _quad(
        (-1.0, -1.0, 1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
        color=color,
        diagonal=diagonal,
    )
    return Scene(
        name="coplanar_quad_%s" % diagonal,
        triangles=triangles,
        cull_backfaces=True,
        description="coplanar square split along diagonal %s" % diagonal,
        tags=("coplanar", "regression"),
    )


def overlap() -> Scene:
    """A small front quad occluding a larger back quad."""
    back = _quad(
        (-1.3, -1.3, -1.0),
        (1.3, -1.3, -1.0),
        (1.3, 1.3, -1.0),
        (-1.3, 1.3, -1.0),
        color=BLUE,
        diagonal="02",
    )
    front = _quad(
        (-0.85, -0.85, 1.0),
        (0.85, -0.85, 1.0),
        (0.85, 0.85, 1.0),
        (-0.85, 0.85, 1.0),
        color=ORANGE,
        diagonal="13",
    )
    return Scene(
        name="overlap",
        triangles=back + front,
        cull_backfaces=True,
        description="front quad at depth 4 over back quad at depth 6",
        tags=("overlap", "regression"),
    )


def mixed_winding() -> Scene:
    """Two adjacent halves of a square with opposite winding.

    Backface culling is disabled so both triangles are rasterized; the
    reversed one receives a flipped geometric normal and therefore a
    different Lambert shade.
    """
    p0 = (-1.2, -1.2, 0.0)
    p1 = (1.2, -1.2, 0.0)
    p2 = (1.2, 1.2, 0.0)
    p3 = (-1.2, 1.2, 0.0)
    first = Triangle(p0, p1, p2, (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), ORANGE)
    # Same covered region as (p0, p2, p3), but with reversed winding.
    second = Triangle(p0, p3, p2, (0.0, 0.0), (0.0, 1.0), (1.0, 1.0), GREEN)
    return Scene(
        name="mixed_winding",
        triangles=[first, second],
        cull_backfaces=False,
        description="adjacent triangles with opposite winding, culling off",
        tags=("winding", "regression"),
    )


def near_clip_textured() -> Scene:
    """A large tilted, UV-mapped surface that extends behind the camera.

    The plane is ``z = 4 + 0.1x + 0.1y`` over ``x,y in [-20, 20]``.  The
    camera at ``(0,0,5)`` therefore sits between the front and back parts of
    the surface; the near plane cuts through both triangles.
    """
    corners = (
        (-20.0, -20.0, 0.0),
        (20.0, -20.0, 4.0),
        (20.0, 20.0, 8.0),
        (-20.0, 20.0, 4.0),
    )
    uvs = (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
    )
    triangles = _quad(*corners, uvs=uvs, color=ORANGE, diagonal="02")
    return Scene(
        name="near_clip_textured",
        triangles=triangles,
        cull_backfaces=True,
        description="UV-mapped tilted plane crossing the near plane",
        tags=("near-clip", "texture", "regression"),
    )


def camera_inside_box() -> Scene:
    """A closed box centred on the camera, so every face crosses the near plane."""
    cx, cy, cz = 0.0, 0.0, 5.0
    h = 2.0
    x0, x1 = cx - h, cx + h
    y0, y1 = cy - h, cy + h
    z0, z1 = cz - h, cz + h

    quads = [
        # front (z = z0, normal -Z, towards the camera)
        ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)),
        # back (z = z1)
        ((x1, y0, z1), (x0, y0, z1), (x0, y1, z1), (x1, y1, z1)),
        # left (x = x0)
        ((x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)),
        # right (x = x1)
        ((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)),
        # bottom (y = y0)
        ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        # top (y = y1)
        ((x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)),
    ]
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    triangles: List[Triangle] = []
    for index, corners in enumerate(quads):
        color = (ORANGE, BLUE, GREEN, GREY, ORANGE, BLUE)[index]
        triangles.extend(_quad(*corners, uvs=uvs, color=color, diagonal="02"))
    return Scene(
        name="camera_inside_box",
        triangles=triangles,
        cull_backfaces=False,
        description="camera inside a box; all faces are near-plane clipped",
        tags=("near-clip", "camera-through", "reference"),
    )


def frustum_edge_shared() -> Scene:
    """A square split along ``x = y``, clipped at the left/bottom frustum planes.

    The world-space diagonal ``x = y`` projects onto pixel centres, so the
    two adjacent triangles share a sample-aligned edge.  The square extends
    beyond the left and bottom frustum planes, so both triangles are clipped
    and the shared diagonal must remain crack-free and single-owned.
    """
    corners = (
        (-3.0, -3.0, 0.0),
        (1.0, -3.0, 0.0),
        (1.0, 1.0, 0.0),
        (-3.0, 1.0, 0.0),
    )
    uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    triangles = _quad(*corners, uvs=uvs, color=ORANGE, diagonal="02")
    return Scene(
        name="frustum_edge_shared",
        triangles=triangles,
        cull_backfaces=True,
        description="square clipped at left/bottom frustum planes with a "
                    "sample-aligned shared diagonal",
        tags=("frustum-boundary", "co-edge", "regression"),
    )


def large_grid(
    cells: int = 192,
    half_extent: float = 2.2,
    with_uv: bool = False,
    color: Optional[Pixel] = None,
) -> Scene:
    """A large flat grid at z=0 (view depth 5), facing the camera."""
    if cells <= 0:
        raise ValueError("cells must be positive")
    step = (2.0 * half_extent) / cells
    triangles: List[Triangle] = []
    for row in range(cells):
        y0 = -half_extent + row * step
        y1 = y0 + step
        for column in range(cells):
            x0 = -half_extent + column * step
            x1 = x0 + step
            p0 = (x0, y0, 0.0)
            p1 = (x1, y0, 0.0)
            p2 = (x1, y1, 0.0)
            p3 = (x0, y1, 0.0)
            if with_uv:
                u0 = column / cells
                u1 = (column + 1) / cells
                v0 = row / cells
                v1 = (row + 1) / cells
                uvs = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            else:
                uvs = None
            triangles.extend(
                _quad(p0, p1, p2, p3, uvs=uvs, color=color, diagonal="02")
            )
    return Scene(
        name="large_grid_%d" % cells,
        triangles=triangles,
        cull_backfaces=True,
        description="%dx%d quad grid (%d triangles)"
                    % (cells, cells, len(triangles)),
        tags=("large", "benchmark"),
    )


def write_grid_obj(path: str, cells: int = 192,
                   half_extent: float = 2.2) -> None:
    """Write a shared-vertex grid OBJ with no texture coordinates."""
    step = (2.0 * half_extent) / cells
    with open(path, "w", encoding="ascii") as handle:
        handle.write("# deterministic %dx%d grid\n" % (cells, cells))
        for row in range(cells + 1):
            y = -half_extent + row * step
            for column in range(cells + 1):
                x = -half_extent + column * step
                handle.write("v %.17g %.17g 0\n" % (x, y))
        for row in range(cells):
            for column in range(cells):
                i0 = row * (cells + 1) + column + 1
                i1 = i0 + 1
                i3 = i0 + (cells + 1)
                i2 = i3 + 1
                handle.write("f %d %d %d\n" % (i0, i1, i2))
                handle.write("f %d %d %d\n" % (i0, i2, i3))


def regression_scenes() -> List[Scene]:
    """Scenes that participate in the pixel-level golden regression."""
    return [
        coplanar_quad("02", ORANGE),
        coplanar_quad("13", ORANGE),
        near_clip_textured(),
        overlap(),
        mixed_winding(),
        frustum_edge_shared(),
    ]


def reference_scenes() -> List[Scene]:
    """Small scenes compared against the independent ray reference."""
    return [
        coplanar_quad("02", ORANGE),
        near_clip_textured(),
        camera_inside_box(),
        frustum_edge_shared(),
        overlap(),
        mixed_winding(),
    ]
