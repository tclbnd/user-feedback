"""Deterministic tiled software rasterizer for offline dataset generation.

Pipeline
--------
1. World-space triangles are transformed to homogeneous clip space
   (:mod:`clipspace`) and clipped against all six frustum planes; UVs are
   interpolated linearly in clip space along clipped edges.
2. Clipped vertices go through one perspective divide.  Screen-space
   interpolation uses a single convention everywhere: ``1/w`` (reciprocal
   view-space depth) and the perspective-divided attributes ``u/w``,
   ``v/w`` vary linearly in screen space.  View depth is recovered as
   ``1 / (1/w)`` and attributes as ``(a/w) / (1/w)``.
3. Primitives are binned into screen tiles.  Each tile is rasterized
   independently at 1x (pixel centre) or 4x (2x2 sub-sample grid)
   sampling, so tiles may be executed in any order and on any number of
   worker processes with bit-identical results.

Determinism rules
-----------------
* Fill rule: triangles are normalized to positive signed area; a sample
  is inside when every edge function is positive, or exactly zero on a
  top-left edge (in y-down screen space an edge ``a -> b`` is top-left
  iff ``dy < 0`` or ``dy == 0 and dx < 0``).  A shared edge therefore
  belongs to exactly one triangle.
* Occlusion is resolved per sample: the winner maximizes the key
  ``(1/w, -primitive_id)``, i.e. nearer fragments win and exact depth
  ties go to the earliest input primitive.  The key is total, so the
  result does not depend on evaluation order.
* Degenerate input triangles (zero geometric normal) and triangles that
  project to zero area are skipped before rasterization, so depth and
  attribute buffers only ever contain finite values.  Uncovered pixels
  read as the far-plane depth.

Shading is flat per face: Lambert diffuse plus ambient, optionally
modulated by a procedural texture sampled at the perspective-correct UV.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing
import struct
from array import array
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import clipspace
import texture as texture_mod

Vertex = Tuple[float, float, float]
Pixel = Tuple[int, int, int]

NEG_INF = float("-inf")
NO_PRIM = 1 << 30

DEFAULT_BACKGROUND: Pixel = (16, 18, 24)
DEFAULT_BASE_COLOR: Pixel = (238, 140, 68)
DEFAULT_LIGHT_DIR: Tuple[float, float, float] = (0.45, 0.8, 1.0)
DEFAULT_AMBIENT = 0.18

SAMPLE_OFFSETS = {
    1: ((0.5, 0.5),),
    4: ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)),
}


@dataclass
class RenderResult:
    """Rendered image plus per-pixel dataset channels and statistics."""

    width: int
    height: int
    samples: int
    tile_size: int
    pixels: List[List[Pixel]]       # resolved RGB
    depth: List[List[float]]        # view-space depth of nearest covered sample
    coverage: List[List[int]]       # per-pixel sub-sample coverage mask
    attr_u: List[List[float]]       # perspective-correct UV of nearest sample
    attr_v: List[List[float]]
    triangle_count: int
    triangles_drawn: int            # post-clip primitives rasterized
    foreground_pixels: int
    foreground_ratio: float
    depth_min: Optional[float]
    depth_max: Optional[float]

    def digest(self) -> str:
        """Stable SHA-256 over color, coverage, depth and UV channels."""
        hasher = hashlib.sha256()
        hasher.update(struct.pack("<IIB", self.width, self.height, self.samples))
        for y in range(self.height):
            for x in range(self.width):
                r, g, b = self.pixels[y][x]
                hasher.update(struct.pack(
                    "<BBBBddd",
                    r, g, b,
                    self.coverage[y][x],
                    self.depth[y][x],
                    self.attr_u[y][x],
                    self.attr_v[y][x],
                ))
        return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

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


def _face_normal(a: Vertex, b: Vertex, c: Vertex) -> Optional[Vertex]:
    """Geometric normal from the triangle's winding (CCW is outward)."""
    return _normalize(_cross(_subtract(b, a), _subtract(c, a)))


def _clip_byte(value: float) -> int:
    value = int(round(value))
    return 0 if value < 0 else 255 if value > 255 else value


# ---------------------------------------------------------------------------
# Primitive preparation (single process)
# ---------------------------------------------------------------------------

# A screen-space primitive record (plain tuple, picklable for workers):
#   (pid, area2,
#    x0, y0, r0, u0, v0,
#    x1, y1, r1, u1, v1,
#    x2, y2, r2, u2, v2,
#    intensity, tl0, tl1, tl2)
# r_i = 1/w_i, u_i = u_i/w_i, v_i = v_i/w_i; tl_i marks top-left edges
# (tl0 for edge v1->v2, tl1 for v2->v0, tl2 for v0->v1).

def _prepare_prims(
    positions: Sequence[Vertex],
    uvs: Sequence[Tuple[float, float]],
    faces: Sequence[Tuple[Tuple[int, int, int], Tuple[int, int, int]]],
    proj: clipspace.Projection,
    cull_backfaces: bool,
    light: Vertex,
    ambient: float,
) -> List[tuple]:
    eye: Vertex = (0.0, 0.0, proj.distance)
    prims: List[tuple] = []

    for face_index, (v_idx, t_idx) in enumerate(faces):
        p0 = positions[v_idx[0]]
        p1 = positions[v_idx[1]]
        p2 = positions[v_idx[2]]

        normal = _face_normal(p0, p1, p2)
        if normal is None:
            continue  # degenerate input triangle
        if cull_backfaces:
            centroid = (
                (p0[0] + p1[0] + p2[0]) / 3.0,
                (p0[1] + p1[1] + p2[1]) / 3.0,
                (p0[2] + p1[2] + p2[2]) / 3.0,
            )
            if _dot(normal, _subtract(eye, centroid)) <= 0.0:
                continue

        intensity = ambient + (1.0 - ambient) * max(0.0, _dot(normal, light))

        clip_verts = []
        for k in range(3):
            t = t_idx[k]
            u, v = uvs[t] if 0 <= t < len(uvs) else (0.0, 0.0)
            clip_verts.append(
                clipspace.world_to_clip(positions[v_idx[k]], u, v, proj)
            )

        for tri in clipspace.clip_triangle(clip_verts):
            prim = _project_prim(tri, face_index, intensity, proj)
            if prim is not None:
                prims.append(prim)
    return prims


def _project_prim(
    tri: Sequence[clipspace.ClipVertex],
    pid: int,
    intensity: float,
    proj: clipspace.Projection,
) -> Optional[tuple]:
    """Perspective divide + viewport map; returns None for zero-area prims."""
    sx = []
    sy = []
    rw = []
    su = []
    sv = []
    for (cx, cy, _cz, w, u, v) in tri:
        # Clipping guarantees w > 0 up to a 1e-9 tolerance band; a vertex
        # with w <= 0 can only appear in that pathological band, so drop
        # the primitive rather than divide by a non-positive w.
        if w <= 0.0:
            return None
        inv_w = 1.0 / w
        ndc_x = cx * inv_w
        ndc_y = cy * inv_w
        sx.append((ndc_x + 1.0) * 0.5 * proj.width)
        sy.append((1.0 - ndc_y) * 0.5 * proj.height)
        rw.append(inv_w)
        su.append(u * inv_w)
        sv.append(v * inv_w)

    area2 = (sx[1] - sx[0]) * (sy[2] - sy[0]) - (sy[1] - sy[0]) * (sx[2] - sx[0])
    if area2 == 0.0:
        return None  # edge-on or degenerate after projection

    order = (0, 1, 2)
    if area2 < 0.0:
        # Normalize winding so the interior is always on the E >= 0 side.
        order = (0, 2, 1)
        area2 = -area2

    xs = [sx[i] for i in order]
    ys = [sy[i] for i in order]
    rs = [rw[i] for i in order]
    us = [su[i] for i in order]
    vs = [sv[i] for i in order]

    # Top-left flags per edge (edge i sits opposite vertex i).
    tl = []
    for i in range(3):
        ax, ay = xs[i], ys[i]
        bx, by = xs[(i + 1) % 3], ys[(i + 1) % 3]
        dx = bx - ax
        dy = by - ay
        tl.append(dy < 0.0 or (dy == 0.0 and dx < 0.0))
    # tl for edge i (vertex i -> vertex i+1); the edge opposite vertex k is
    # edge (k+1)%3, so remap: tl_opposite[k] = tl[(k+1)%3].
    tl0 = tl[1]
    tl1 = tl[2]
    tl2 = tl[0]

    return (
        pid, area2,
        xs[0], ys[0], rs[0], us[0], vs[0],
        xs[1], ys[1], rs[1], us[1], vs[1],
        xs[2], ys[2], rs[2], us[2], vs[2],
        intensity, tl0, tl1, tl2,
    )


# ---------------------------------------------------------------------------
# Tile rasterization (may run in worker processes)
# ---------------------------------------------------------------------------

def _shade(base: Pixel, intensity: float) -> Tuple[int, int, int]:
    return (
        _clip_byte(base[0] * intensity),
        _clip_byte(base[1] * intensity),
        _clip_byte(base[2] * intensity),
    )


def _rasterize_tile(task: tuple) -> tuple:
    (
        tile_x, tile_y, x_begin, y_begin, x_end, y_end,
        prims, config,
    ) = task
    (
        width, height, sample_offsets, background, base_color, texture_spec,
        far,
    ) = config

    n_samples = len(sample_offsets)
    tile_w = x_end - x_begin
    tile_h = y_end - y_begin
    n_slots = tile_w * tile_h * n_samples

    s_invw = [NEG_INF] * n_slots
    s_pid = [NO_PRIM] * n_slots
    s_u = [0.0] * n_slots
    s_v = [0.0] * n_slots
    s_intensity = [0.0] * n_slots

    for prim in prims:
        (
            pid, area2,
            x0, y0, r0, u0, v0,
            x1, y1, r1, u1, v1,
            x2, y2, r2, u2, v2,
            intensity, tl0, tl1, tl2,
        ) = prim

        min_x = min(x0, x1, x2)
        max_x = max(x0, x1, x2)
        min_y = min(y0, y1, y2)
        max_y = max(y0, y1, y2)
        px_begin = max(x_begin, int(math.floor(min_x)))
        px_end = min(x_end - 1, int(math.ceil(max_x)))
        py_begin = max(y_begin, int(math.floor(min_y)))
        py_end = min(y_end - 1, int(math.ceil(max_y)))
        if px_end < px_begin or py_end < py_begin:
            continue

        # Edge function coefficients: E0 (opposite v0) uses edge v1->v2.
        a0 = y1 - y2
        b0 = x2 - x1
        c0 = x1 * y2 - x2 * y1
        a1 = y2 - y0
        b1 = x0 - x2
        c1 = x2 * y0 - x0 * y2
        a2 = y0 - y1
        b2 = x1 - x0
        c2 = x0 * y1 - x1 * y0

        neg_pid = -pid

        for py in range(py_begin, py_end + 1):
            row = (py - y_begin) * tile_w
            for px in range(px_begin, px_end + 1):
                base_slot = (row + (px - x_begin)) * n_samples
                for si in range(n_samples):
                    ox, oy = sample_offsets[si]
                    qx = px + ox
                    qy = py + oy

                    e0 = a0 * qx + b0 * qy + c0
                    if e0 < 0.0 or (e0 == 0.0 and not tl0):
                        continue
                    e1 = a1 * qx + b1 * qy + c1
                    if e1 < 0.0 or (e1 == 0.0 and not tl1):
                        continue
                    e2 = a2 * qx + b2 * qy + c2
                    if e2 < 0.0 or (e2 == 0.0 and not tl2):
                        continue

                    w0 = e0 / area2
                    w1 = e1 / area2
                    w2 = 1.0 - w0 - w1
                    inv_w = w0 * r0 + w1 * r1 + w2 * r2

                    slot = base_slot + si
                    # Total order: nearer (larger 1/w) wins; ties go to the
                    # smaller primitive id.  Order-independent by construction.
                    if inv_w < s_invw[slot]:
                        continue
                    if inv_w == s_invw[slot] and neg_pid <= -s_pid[slot]:
                        continue
                    s_invw[slot] = inv_w
                    s_pid[slot] = pid
                    s_u[slot] = (w0 * u0 + w1 * u1 + w2 * u2) / inv_w
                    s_v[slot] = (w0 * v0 + w1 * v1 + w2 * v2) / inv_w
                    s_intensity[slot] = intensity

    # Resolve samples into per-pixel color, coverage, depth and UV.
    color = bytearray(tile_w * tile_h * 3)
    coverage = bytearray(tile_w * tile_h)
    depth = array("d", [far]) * (tile_w * tile_h)
    attr_u = array("d", [0.0]) * (tile_w * tile_h)
    attr_v = array("d", [0.0]) * (tile_w * tile_h)

    bg_r, bg_g, bg_b = background
    for pixel in range(tile_w * tile_h):
        base_slot = pixel * n_samples
        mask = 0
        sum_r = sum_g = sum_b = 0
        best_invw = NEG_INF
        best_slot = -1
        for si in range(n_samples):
            slot = base_slot + si
            if s_pid[slot] == NO_PRIM:
                continue
            mask |= 1 << si
            if texture_spec is not None:
                tex = texture_mod.sample(texture_spec, s_u[slot], s_v[slot])
            else:
                tex = base_color
            inten = s_intensity[slot]
            sum_r += _clip_byte(tex[0] * inten)
            sum_g += _clip_byte(tex[1] * inten)
            sum_b += _clip_byte(tex[2] * inten)
            if s_invw[slot] > best_invw:
                best_invw = s_invw[slot]
                best_slot = slot

        out = pixel * 3
        if mask == 0:
            color[out] = bg_r
            color[out + 1] = bg_g
            color[out + 2] = bg_b
            continue

        uncovered = n_samples - bin(mask).count("1")
        color[out] = (sum_r + uncovered * bg_r) // n_samples
        color[out + 1] = (sum_g + uncovered * bg_g) // n_samples
        color[out + 2] = (sum_b + uncovered * bg_b) // n_samples
        coverage[pixel] = mask
        depth[pixel] = 1.0 / best_invw
        attr_u[pixel] = s_u[best_slot]
        attr_v[pixel] = s_v[best_slot]

    return (tile_x, tile_y, bytes(color), bytes(coverage), depth, attr_u, attr_v)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    positions: Sequence[Vertex],
    uvs: Sequence[Tuple[float, float]],
    faces: Sequence[Tuple[Tuple[int, int, int], Tuple[int, int, int]]],
    size: int = 512,
    fov_deg: float = 45.0,
    distance: float = 5.0,
    near: float = 0.25,
    far: float = 500.0,
    samples: int = 1,
    tile_size: int = 64,
    workers: int = 1,
    tile_order: str = "forward",
    cull_backfaces: bool = True,
    light_dir: Sequence[float] = DEFAULT_LIGHT_DIR,
    background: Pixel = DEFAULT_BACKGROUND,
    base_color: Pixel = DEFAULT_BASE_COLOR,
    ambient: float = DEFAULT_AMBIENT,
    texture: Optional[tuple] = None,
) -> RenderResult:
    """Render a mesh to color/depth/coverage/UV buffers, deterministically.

    ``faces`` are ``((v0, v1, v2), (t0, t1, t2))`` pairs as produced by
    ``obj_loader.load_mesh``; texture indices may be -1.  ``tile_order``
    is ``"forward"``, ``"reverse"`` or ``"shuffle"`` and only affects the
    execution order of tiles, never the output.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if samples not in SAMPLE_OFFSETS:
        raise ValueError("samples must be one of %s" % sorted(SAMPLE_OFFSETS))
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    if not 0.0 <= ambient <= 1.0:
        raise ValueError("ambient must be between 0 and 1")

    light = _normalize(
        (float(light_dir[0]), float(light_dir[1]), float(light_dir[2]))
    )
    if light is None:
        raise ValueError("light direction must be non-zero")

    width = height = int(size)
    proj = clipspace.make_projection(
        width, height, fov_deg=fov_deg, distance=distance, near=near, far=far
    )
    sample_offsets = SAMPLE_OFFSETS[samples]

    prims = _prepare_prims(
        positions, uvs, faces, proj, cull_backfaces, light, ambient
    )

    # Bin primitives into tiles.
    tiles_x = (width + tile_size - 1) // tile_size
    tiles_y = (height + tile_size - 1) // tile_size
    tile_prims: Dict[int, List[tuple]] = {}
    for prim in prims:
        xs = (prim[2], prim[7], prim[12])
        ys = (prim[3], prim[8], prim[13])
        tx0 = max(0, int(math.floor(min(xs))) // tile_size)
        tx1 = min(tiles_x - 1, int(math.ceil(max(xs))) // tile_size)
        ty0 = max(0, int(math.floor(min(ys))) // tile_size)
        ty1 = min(tiles_y - 1, int(math.ceil(max(ys))) // tile_size)
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile_prims.setdefault(ty * tiles_x + tx, []).append(prim)

    config = (width, height, sample_offsets, background, base_color, texture, far)
    tasks = []
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            index = ty * tiles_x + tx
            tasks.append((
                tx, ty,
                tx * tile_size, ty * tile_size,
                min(width, (tx + 1) * tile_size),
                min(height, (ty + 1) * tile_size),
                tile_prims.get(index, []),
                config,
            ))

    if tile_order == "reverse":
        tasks.reverse()
    elif tile_order == "shuffle":
        import random

        random.Random(0x5EED).shuffle(tasks)
    elif tile_order != "forward":
        raise ValueError("unknown tile order %r" % tile_order)

    if workers > 1 and len(tasks) > 1:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=workers) as pool:
            results = pool.map(_rasterize_tile, tasks)
    else:
        results = [_rasterize_tile(task) for task in tasks]

    # Assemble the full image; tiles are disjoint, so assembly order is
    # irrelevant to the result.
    pixels = [[background] * width for _ in range(height)]
    depth = [[far] * width for _ in range(height)]
    coverage = [[0] * width for _ in range(height)]
    attr_u = [[0.0] * width for _ in range(height)]
    attr_v = [[0.0] * width for _ in range(height)]

    for tile_x, tile_y, color, cov, t_depth, t_u, t_v in results:
        x_begin = tile_x * tile_size
        y_begin = tile_y * tile_size
        x_end = min(width, x_begin + tile_size)
        y_end = min(height, y_begin + tile_size)
        tile_w = x_end - x_begin
        for py in range(y_begin, y_end):
            row_off = (py - y_begin) * tile_w
            color_row = pixels[py]
            cov_row = coverage[py]
            depth_row = depth[py]
            u_row = attr_u[py]
            v_row = attr_v[py]
            for px in range(x_begin, x_end):
                cell = row_off + (px - x_begin)
                out = cell * 3
                color_row[px] = (color[out], color[out + 1], color[out + 2])
                cov_row[px] = cov[cell]
                depth_row[px] = t_depth[cell]
                u_row[px] = t_u[cell]
                v_row[px] = t_v[cell]

    foreground = 0
    depth_min = math.inf
    depth_max = NEG_INF
    full_mask = (1 << len(sample_offsets)) - 1
    for py in range(height):
        for px in range(width):
            if coverage[py][px] == 0:
                continue
            foreground += 1
            d = depth[py][px]
            if d < depth_min:
                depth_min = d
            if d > depth_max:
                depth_max = d

    total = width * height
    return RenderResult(
        width=width,
        height=height,
        samples=samples,
        tile_size=tile_size,
        pixels=pixels,
        depth=depth,
        coverage=coverage,
        attr_u=attr_u,
        attr_v=attr_v,
        triangle_count=len(faces),
        triangles_drawn=len(prims),
        foreground_pixels=foreground,
        foreground_ratio=foreground / float(total),
        depth_min=depth_min if foreground else None,
        depth_max=depth_max if foreground else None,
    )
