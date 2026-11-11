"""Deterministic scene generators for regression and stress tests.

Every generator returns ``(positions, uvs, faces)`` in the same shape as
``obj_loader.Mesh`` so scenes can be fed straight into
``rasterizer.render`` and ``reference``.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Vertex = Tuple[float, float, float]
UV = Tuple[float, float]
Face = Tuple[Tuple[int, int, int], Tuple[int, int, int]]

Scene = Tuple[List[Vertex], List[UV], List[Face]]


def _quad(
    positions: List[Vertex],
    uvs: List[UV],
    faces: List[Face],
    corners: Sequence[Vertex],
    corner_uvs: Sequence[UV],
    split: str = "a",
) -> None:
    """Append a quad as two triangles; ``split`` selects the diagonal."""
    base_v = len(positions)
    base_t = len(uvs)
    positions.extend(corners)
    uvs.extend(corner_uvs)
    v = (base_v, base_v + 1, base_v + 2, base_v + 3)
    t = (base_t, base_t + 1, base_t + 2, base_t + 3)
    if split == "a":  # diagonal 0-2
        faces.append(((v[0], v[1], v[2]), (t[0], t[1], t[2])))
        faces.append(((v[0], v[2], v[3]), (t[0], t[2], t[3])))
    elif split == "b":  # diagonal 1-3
        faces.append(((v[0], v[1], v[3]), (t[0], t[1], t[3])))
        faces.append(((v[1], v[2], v[3]), (t[1], t[2], t[3])))
    else:
        raise ValueError("split must be 'a' or 'b'")


def coplanar_quad(split: str = "a") -> Scene:
    """One textured quad at z = 0, triangulated along either diagonal."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    corners = [
        (-1.31, -1.07, 0.0),
        (1.29, -1.09, 0.0),
        (1.33, 1.11, 0.0),
        (-1.27, 1.13, 0.0),
    ]
    corner_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    _quad(positions, uvs, faces, corners, corner_uvs, split=split)
    return positions, uvs, faces


def textured_near_crossing(distance: float = 5.0, near: float = 0.25) -> Scene:
    """Textured quad tilted through the near plane.

    The quad spans view depths on both sides of ``near`` so clipping must
    cut it and interpolate UVs along the clipped edges.
    """
    z_near = distance - near
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    corners = [
        (-2.4, -1.2, z_near - 0.9),   # visible side
        (2.4, -1.1, z_near - 0.7),
        (2.2, 1.3, z_near + 1.1),     # behind the near plane
        (-2.3, 1.2, z_near + 0.8),
    ]
    corner_uvs = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    _quad(positions, uvs, faces, corners, corner_uvs, split="a")
    return positions, uvs, faces


def front_back_overlap() -> Scene:
    """Two overlapping quads at different depths (occlusion check)."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    _quad(
        positions, uvs, faces,
        [(-1.6, -1.2, -0.6), (1.2, -1.2, -0.6), (1.2, 1.2, -0.6), (-1.6, 1.2, -0.6)],
        [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)],
        split="a",
    )
    _quad(
        positions, uvs, faces,
        [(-0.9, -0.8, 0.4), (1.5, -0.8, 0.4), (1.5, 0.9, 0.4), (-0.9, 0.9, 0.4)],
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        split="b",
    )
    return positions, uvs, faces


def mixed_winding() -> Scene:
    """2x2 grid of quads; alternating triangles are wound backwards."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    for gy in range(2):
        for gx in range(2):
            x0 = -1.6 + gx * 1.6
            y0 = -1.2 + gy * 1.2
            corners = [
                (x0, y0, 0.0),
                (x0 + 1.6, y0, 0.0),
                (x0 + 1.6, y0 + 1.2, 0.0),
                (x0, y0 + 1.2, 0.0),
            ]
            corner_uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
            base_v = len(positions)
            base_t = len(uvs)
            positions.extend(corners)
            uvs.extend(corner_uvs)
            v = tuple(base_v + i for i in range(4))
            t = tuple(base_t + i for i in range(4))
            if (gx + gy) % 2 == 0:
                faces.append(((v[0], v[1], v[2]), (t[0], t[1], t[2])))
                faces.append(((v[0], v[2], v[3]), (t[0], t[2], t[3])))
            else:  # reversed winding on both triangles
                faces.append(((v[2], v[1], v[0]), (t[2], t[1], t[0])))
                faces.append(((v[3], v[2], v[0]), (t[3], t[2], t[0])))
    return positions, uvs, faces


def camera_through_surface(distance: float = 5.0) -> Scene:
    """Large planes crossing the near plane and the camera itself."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    quads = [
        # Vertical plane straddling the camera position along z.
        [(-1.0, -3.0, distance - 2.5), (-1.0, 3.0, distance - 2.5),
         (-1.0, 3.0, distance + 2.5), (-1.0, -3.0, distance + 2.5)],
        # Horizontal plane in front, crossing only the near plane.
        [(-3.0, -0.8, distance - 3.0), (3.0, -0.8, distance - 3.0),
         (3.0, -0.8, distance + 0.6), (-3.0, -0.8, distance + 0.6)],
        # Tilted plane fully behind the camera (must vanish).
        [(-2.0, -2.0, distance + 1.0), (2.0, -2.0, distance + 1.0),
         (2.0, 2.0, distance + 1.0), (-2.0, 2.0, distance + 1.0)],
    ]
    for corners in quads:
        _quad(
            positions, uvs, faces, corners,
            [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
            split="a",
        )
    return positions, uvs, faces


def frustum_boundary_shared_edge(distance: float = 5.0) -> Scene:
    """Shared-edge triangles at the frustum boundary and on pixel centres.

    Designed for a 90-degree vertical FOV (tan(fov/2) == 1 within 1 ulp)
    and a square image, so the right frustum plane is ``x == view_depth``:

    * Triangles 0-1 form a planar quad whose shared diagonal lies just
      inside the right frustum plane while the far corner pokes through
      it: clipping must cut one triangle and leave the shared edge
      bit-identical in both.
    * Triangles 2-3 form a quad at view depth 2 whose shared diagonal
      projects exactly onto pixel centres (screen line y == x at size
      256), exercising the top-left ownership rule with exact zeros.
    """
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []

    # Pair 1: planar quad P-R-Q-S straddling the right frustum plane;
    # the shared edge is the diagonal P-Q.
    p = (1.8, 0.2, distance - 2.0)
    r = (3.5, 0.6, distance - 2.8)   # outside the right plane (x > depth)
    q = (2.2, 1.4, distance - 3.0)
    s = (0.5, 1.0, distance - 2.2)   # s = p + q - r: planar parallelogram
    positions.extend([p, r, q, s])
    uvs.extend([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    faces.append(((0, 1, 2), (0, 1, 2)))  # P-R-Q, crosses the right plane
    faces.append(((0, 2, 3), (0, 2, 3)))  # P-Q-S, fully inside

    # Pair 2: interior quad at view depth 2, diagonal through pixel centres.
    z = distance - 2.0
    s1 = (-1.0, 1.0, z)
    s2 = (1.0, 1.0, z)
    s3 = (1.0, -1.0, z)
    s4 = (-1.0, -1.0, z)
    base = len(positions)
    positions.extend([s1, s2, s3, s4])
    uvs.extend([(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)])
    faces.append(((base + 0, base + 1, base + 2), (base + 0, base + 1, base + 2)))
    faces.append(((base + 0, base + 2, base + 3), (base + 0, base + 2, base + 3)))
    return positions, uvs, faces


def large_mesh(grid: int = 100, distance: float = 5.0) -> Scene:
    """Deterministic heightfield: ``grid`` x ``grid`` quads, UV-tiled."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []
    extent = 3.2
    for gy in range(grid + 1):
        for gx in range(grid + 1):
            x = -extent + 2.0 * extent * gx / grid
            y = -extent + 2.0 * extent * gy / grid
            z = 0.35 * math.sin(gx * 0.7) * math.cos(gy * 0.53) \
                + 0.15 * math.sin((gx + gy) * 1.7)
            positions.append((x, y, z))
            uvs.append((gx / 8.0, gy / 8.0))

    def vid(gx: int, gy: int) -> int:
        return gy * (grid + 1) + gx

    for gy in range(grid):
        for gx in range(grid):
            v0 = vid(gx, gy)
            v1 = vid(gx + 1, gy)
            v2 = vid(gx + 1, gy + 1)
            v3 = vid(gx, gy + 1)
            faces.append(((v0, v1, v2), (v0, v1, v2)))
            faces.append(((v0, v2, v3), (v0, v2, v3)))
    return positions, uvs, faces
