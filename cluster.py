#!/usr/bin/env python3
"""Conservative light-to-cluster assignment for a view-frustum cluster grid.

Usage:
    python3 cluster.py input.json output.json

Input (see README.md):
    {"grid": [gx, gy, gz],
     "bounds": [[x, y, z]min, [x, y, z]max],
     "lights": [{"pos": [x, y, z], "r": f,
                 "type": "point"|"spot", "dir": [x, y, z], "half": rad}, ...],
     "seed": 1}

Optional keys:

    "camera": {"pos": [...], "target": [...], "up": [...],
               "fov_y": rad, "aspect": f, "near": f, "far": f}
    "depth_slices": n        # overrides grid[2] on the frustum path
    "edges": [[...], [...], [...]]  # explicit per-axis cell boundaries

When a camera is supplied the grid is the view-frustum tessellation: ``gx`` by
``gy`` screen tiles over the frustum, and ``gz`` depth slices spaced
logarithmically (slice 0 nearest).  Without a camera the documented uniform
grid over ``bounds`` is used, with x as the fastest axis
(``cid = (k * gy + j) * gx + i``).

A light is appended to every cluster its influence volume touches:

* point lights use the sphere ``(pos, r)``;
* spot lights shrink that sphere by the cone half angle,
  ``r_eff = r * sin(half)``, which is the conservative proxy for the cone used
  by this format.

The frustum path tests against the exact six half-spaces of the frustum patch
rather than its bounding box, which more than halves the spurious hits on
diagonal tiles while remaining conservative (a light that can touch any point
of the patch always survives the test).

All intersections are closed and the per-axis candidate range is padded, so
spheres crossing cell faces end up in every touched cell and floating-point
round-off cannot drop a pair.  Degenerate input (zero-size bounds, zero grid
resolution, radius 0, half angle 0 or >= pi/2, reversed bounds, empty depth
slices) is accepted and still produces a well-formed, conservative result.

Assignment depends only on the *geometry* of each light: reordering the input
list changes the reported ``light_id`` values (they are input indices) but not
which light volumes land in which cluster, so the order-independent digest
stays the same.

Output:
    {"clusters": [[light_id, ...] per cluster],
     "counts": [per-cluster light count],
     "digest": "sha1",
     "stats": {...},
     "conflicts": {...}}   # same payload, kept for compatibility
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import sys
from typing import List, Optional, Sequence, Tuple

# Relative slack.  It makes the closed intersection test tolerant of a few
# ulps of round-off in the cell-edge arithmetic; at normal scene scales it is
# far smaller than any meaningful geometric feature, so it cannot inflate the
# per-cluster light sets in practice.
_REL_SLACK = 1.0e-12


def _num(value, default: float = 0.0) -> float:
    """Best-effort finite float conversion with -0.0 normalised to 0.0."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return 0.0 if out == 0.0 else out


def _vec3(value, default: Tuple[float, float, float]) -> Tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return default
    return (_num(value[0], default[0]),
            _num(value[1], default[1]),
            _num(value[2], default[2]))


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _influence(light: dict):
    """Return (centre, effective radius, kind, half angle, direction).

    Spot lights are represented by the shrunken sphere mandated by the input
    format.  A half angle outside [0, pi/2] is clamped: a negative angle would
    collapse (or invert) the volume, while angles past 90 degrees are not a
    cone and must not shrink the radius below ``r``.  A spot with no ``half``
    at all carries no cone information, so it falls back to the full sphere
    (``kind`` comes back as ``"point"``); assuming ``half = 0`` would silently
    drop an otherwise bright light.
    """
    kind = str(light.get("type") or "point").strip().lower()
    pos = _vec3(light.get("pos"), (0.0, 0.0, 0.0))
    radius = abs(_num(light.get("r"), 0.0))

    half: Optional[float] = None
    direction: Optional[Tuple[float, float, float]] = None
    if kind == "spot":
        if light.get("half") is None:
            kind = "point"
        else:
            half = _num(light.get("half"), 0.0)
            half = 0.0 if half < 0.0 else (0.5 * math.pi if half > 0.5 * math.pi else half)
            radius *= math.sin(half)
            direction = _vec3(light.get("dir"), (0.0, 0.0, -1.0))
    return pos, radius, kind, half, direction


def _identity(light: dict) -> str:
    """Canonical, JSON-serialisable identity of a light's influence volume."""
    pos, radius, kind, half, direction = _influence(light)
    if kind == "spot":
        payload = ["spot", list(pos), radius, list(direction), half]
    else:
        payload = [kind, list(pos), radius]
    # json.dumps renders floats deterministically, so this string is stable
    # across runs and independent of the light's position in the input array.
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _axis_edges(lo: float, hi: float, count: int) -> List[float]:
    """Cell boundaries on one axis (``count + 1`` values)."""
    if count <= 0:
        return []
    if hi < lo:
        lo, hi = hi, lo
    if count == 1:
        return [lo, hi]
    extent = hi - lo
    return [lo + extent * i / count for i in range(count + 1)]


def _candidate_span(edges: Sequence[float],
                    swept_lo: float, swept_hi: float) -> Tuple[int, int]:
    """Inclusive cell-index range on one axis that may touch the swept slab.

    ``edges`` holds the ``count + 1`` (non-decreasing) cell boundaries, so this
    works for uniform, logarithmic or otherwise non-uniform tessellations, and
    for degenerate zero-width cells.  Returns ``(0, -1)`` when no cell can be
    touched.  The range is a superset of the touched cells; the exact
    sphere/box test performs the final rejection.
    """
    count = len(edges) - 1
    if count <= 0:
        return 0, -1
    pad = _REL_SLACK * max(1.0, abs(edges[0]), abs(edges[-1]),
                           abs(swept_lo), abs(swept_hi))
    # bisect_right at the low end keeps cells whose far edge equals swept_lo,
    # and bisect_left at the high end keeps cells starting exactly at swept_hi;
    # both cases are real closed-box contacts.
    first = bisect.bisect_right(edges, swept_lo - pad) - 1
    last = bisect.bisect_left(edges, swept_hi + pad)
    if first < 0:
        first = 0
    if last > count - 1:
        last = count - 1
    if last < 0 or first > count - 1:
        return 0, -1
    return first, last


def _sphere_hits_box(center: Sequence[float], radius: float,
                     box_lo: Sequence[float], box_hi: Sequence[float],
                     slack: float) -> bool:
    """Closed sphere/AABB overlap test (squared distance to the box)."""
    dist2 = 0.0
    for axis in range(3):
        value = center[axis]
        lo = box_lo[axis]
        hi = box_hi[axis]
        if value < lo:
            delta = lo - value
            dist2 += delta * delta
        elif value > hi:
            delta = value - hi
            dist2 += delta * delta
    reach = radius + slack
    return dist2 <= reach * reach


def _axis_bounds(edges: Sequence[float], index: int) -> Tuple[float, float]:
    lo = edges[index]
    hi = edges[index + 1]
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _normalise(vector: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    length = math.sqrt(vector[0] * vector[0] + vector[1] * vector[1]
                       + vector[2] * vector[2])
    if not math.isfinite(length) or length <= 0.0:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _log_depth_edges(near: float, far: float, slices: int) -> List[float]:
    """Logarithmically spaced depth slice boundaries (``slices + 1`` values).

    Near/far are clamped to a usable range so that near <= 0 or far <= near
    still yields a non-decreasing edge list (possibly zero-width slices, which
    downstream code treats as degenerate clusters rather than errors).
    """
    if slices <= 0:
        return []
    if near < 0.0:
        near = 0.0
    if far < near:
        far = near
    if slices == 1 or far <= 0.0 or near <= 0.0:
        # Log spacing needs positive depths; fall back to linear (and to a
        # degenerate stack when near == far).
        return [near + (far - near) * i / slices for i in range(slices + 1)]
    ratio = far / near
    return [near * ratio ** (i / slices) for i in range(slices + 1)]


def _frustum_bases(camera: dict):
    """Camera basis (origin, forward, right, up) and fov/aspect/aspect data."""
    origin = _vec3(camera.get("pos"), (0.0, 0.0, 0.0))
    target = _vec3(camera.get("target"), (0.0, 0.0, -1.0))
    forward = _normalise([target[a] - origin[a] for a in range(3)])
    if forward is None:
        forward = (0.0, 0.0, -1.0)

    up_hint = _normalise(_vec3(camera.get("up"), (0.0, 1.0, 0.0))) \
        or (0.0, 1.0, 0.0)
    right = _normalise(_cross(forward, up_hint))
    if right is None:
        # Camera looking exactly along the up vector: pick any perpendicular.
        helper = (1.0, 0.0, 0.0) if abs(forward[0]) < 0.9 else (0.0, 0.0, 1.0)
        right = _normalise(_cross(forward, helper)) or (1.0, 0.0, 0.0)
    up = _normalise(_cross(right, forward)) or (0.0, 1.0, 0.0)

    fov_y = _num(camera.get("fov_y"), math.radians(60.0))
    if not (0.0 < fov_y < math.pi):
        fov_y = math.radians(60.0)
    aspect = _num(camera.get("aspect"), 1.0)
    if not math.isfinite(aspect) or aspect <= 0.0:
        aspect = 1.0
    near = _num(camera.get("near"), 0.1)
    far = _num(camera.get("far"), 100.0)
    if far < near:
        far = near
    return origin, forward, right, up, fov_y, aspect, near, far


def _frustum_regions(camera: dict, tiles_x: int, tiles_y: int,
                     slices: int) -> List[dict]:
    """Cluster regions of the view-frustum grid.

    Region order is ``(k * ty + j) * tx + i``.  Each region carries

    * ``lo``/``hi``: the AABB of the frustum patch (used for coarse indexing);
    * ``planes``: the six half-spaces ``a . p <= b`` (camera space, unit
      normals) whose intersection *is* the patch, so membership can be decided
      on the actual patch instead of its loose bounding box.

    Testing "centre within ``radius`` of every half-space" is conservative:
    a true intersection point lies in all six half-spaces, so it is at most
    ``radius`` outside each of them.  It therefore never drops a light, while
    rejecting spheres that only reach the patch's AABB corners.
    """
    (origin, forward, right, up,
     fov_y, aspect, near, far) = _frustum_bases(camera)
    depths = _log_depth_edges(near, far, slices)
    half_h = math.tan(0.5 * fov_y)
    half_w = half_h * aspect

    regions: List[dict] = []
    for k in range(slices):
        z0 = depths[k]
        z1 = depths[k + 1]
        for j in range(tiles_y):
            # Row j spans NDC y in [1 - 2(j+1)/ty, 1 - 2j/ty].
            y_hi = half_h * (1.0 - 2.0 * j / tiles_y)
            y_lo = half_h * (1.0 - 2.0 * (j + 1) / tiles_y)
            for i in range(tiles_x):
                x_lo = half_w * (2.0 * i / tiles_x - 1.0)
                x_hi = half_w * (2.0 * (i + 1) / tiles_x - 1.0)

                lo = [math.inf, math.inf, math.inf]
                hi = [-math.inf, -math.inf, -math.inf]
                for depth in (z0, z1):
                    for nx in (x_lo, x_hi):
                        for ny in (y_lo, y_hi):
                            for axis in range(3):
                                value = (origin[axis]
                                         + forward[axis] * depth
                                         + right[axis] * (nx * depth)
                                         + up[axis] * (ny * depth))
                                if value < lo[axis]:
                                    lo[axis] = value
                                if value > hi[axis]:
                                    hi[axis] = value

                raw = (
                    ((1.0, 0.0, -x_hi), 0.0),   # x <= x_hi * z
                    ((-1.0, 0.0, x_lo), 0.0),   # x >= x_lo * z
                    ((0.0, 1.0, -y_hi), 0.0),   # y <= y_hi * z
                    ((0.0, -1.0, y_lo), 0.0),   # y >= y_lo * z
                    ((0.0, 0.0, -1.0), -z0),    # z >= z0
                    ((0.0, 0.0, 1.0), z1),      # z <= z1
                )
                planes = []
                for normal, offset in raw:
                    length = math.sqrt(normal[0] * normal[0]
                                       + normal[1] * normal[1]
                                       + normal[2] * normal[2])
                    if length <= 0.0:
                        continue
                    nx, ny, nz = (normal[0] / length,
                                  normal[1] / length,
                                  normal[2] / length)
                    # Camera-space half-space -> world space.  A camera-space
                    # point is (right.(p-o), up.(p-o), forward.(p-o)), so the
                    # world normal is the same combination of the basis axes.
                    world = [nx * right[a] + ny * up[a] + nz * forward[a]
                             for a in range(3)]
                    world_offset = offset / length + sum(
                        world[a] * origin[a] for a in range(3))
                    planes.append((tuple(world), world_offset))

                regions.append({
                    "lo": tuple(lo),
                    "hi": tuple(hi),
                    "planes": planes,
                })
    return regions


def _sphere_hits_region(center, radius, region, slack) -> bool:
    """Conservative sphere/physical-region test."""
    if not _sphere_hits_box(center, radius, region["lo"], region["hi"], slack):
        return False
    planes = region["planes"]
    if planes:
        reach = radius + slack
        for normal, offset in planes:
            signed = (normal[0] * center[0] + normal[1] * center[1]
                      + normal[2] * center[2] - offset)
            if signed > reach:
                return False
    return True


class _BoxIndex:
    """Coarse uniform hash over cluster AABBs.

    Cluster boxes are inserted into every bucket their AABB touches; queries
    return a superset of the boxes overlapping the query AABB, which is then
    filtered by the exact sphere/box test.  This keeps the frustum path from
    degrading to ``lights * clusters`` tests while leaving the conservative
    outcome unchanged.
    """

    def __init__(self, boxes: Sequence[Tuple[Sequence[float],
                                             Sequence[float]]]):
        self._boxes = boxes
        count = len(boxes)
        if count == 0:
            self._resolution = (1, 1, 1)
            self._origin = (0.0, 0.0, 0.0)
            self._size = (1.0, 1.0, 1.0)
            self._buckets: dict = {}
            return

        lo = [min(box[0][a] for box in boxes) for a in range(3)]
        hi = [max(box[1][a] for box in boxes) for a in range(3)]
        # Aim for a handful of boxes per bucket; clamp to keep memory bounded.
        side = max(1, min(64, int(round(count ** (1.0 / 3.0)))))
        self._resolution = (side, side, side)
        self._origin = tuple(lo)
        self._size = tuple(max(hi[a] - lo[a], 1.0e-12) for a in range(3))
        self._buckets = {}
        for index, (box_lo, box_hi) in enumerate(boxes):
            for key in self._keys(box_lo, box_hi):
                self._buckets.setdefault(key, []).append(index)

    def _axis_cells(self, axis: int, low: float, high: float) -> range:
        origin = self._origin[axis]
        size = self._size[axis]
        res = self._resolution[axis]
        first = int(math.floor((low - origin) / size * res))
        last = int(math.floor((high - origin) / size * res))
        first = max(0, min(res - 1, first))
        last = max(0, min(res - 1, last))
        if last < first:
            first, last = last, first
        return range(first, last + 1)

    def _keys(self, box_lo, box_hi):
        xs = self._axis_cells(0, box_lo[0], box_hi[0])
        ys = self._axis_cells(1, box_lo[1], box_hi[1])
        zs = self._axis_cells(2, box_lo[2], box_hi[2])
        for z in zs:
            for y in ys:
                for x in xs:
                    yield (x, y, z)

    def candidates(self, box_lo, box_hi) -> List[int]:
        found = set()
        for key in self._keys(box_lo, box_hi):
            bucket = self._buckets.get(key)
            if bucket:
                found.update(bucket)
        return sorted(found)


def _assign(lights: List[dict], regions_of) -> Tuple[List[List[int]], List[str]]:
    """Attach each light to every cluster region its influence sphere touches.

    ``regions_of`` returns a stable sequence of region dicts with ``lo``/``hi``
    AABBs and optional exact ``planes``.  Lists come out in ascending light-id
    order, so a given light ordering always yields the same per-cluster
    sequence.
    """
    regions = regions_of()
    clusters: List[List[int]] = [[] for _ in range(len(regions))]
    identities: List[str] = []
    index = _BoxIndex([(region["lo"], region["hi"]) for region in regions])
    for light_id, light in enumerate(lights):
        center, radius, _kind, _half, _direction = _influence(light)
        identities.append(_identity(light))
        slack = _REL_SLACK * max(
            1.0, abs(radius),
            abs(center[0]), abs(center[1]), abs(center[2]),
        )
        swept_lo = [center[a] - radius - slack for a in range(3)]
        swept_hi = [center[a] + radius + slack for a in range(3)]
        for region_index in index.candidates(swept_lo, swept_hi):
            region = regions[region_index]
            if _sphere_hits_region(center, radius, region, slack):
                clusters[region_index].append(light_id)
    return clusters, identities


def build_clusters(req: dict) -> dict:
    """Build the per-cluster light lists for one request document."""
    grid_raw = req.get("grid")
    if not isinstance(grid_raw, (list, tuple)) or len(grid_raw) < 3:
        grid_raw = [1, 1, 1]
    counts_xyz = [max(0, _int(grid_raw[axis], 1)) for axis in range(3)]
    gx, gy, gz = counts_xyz
    total = gx * gy * gz

    lights = req.get("lights")
    if not isinstance(lights, list):
        lights = []
    lights = [item if isinstance(item, dict) else {} for item in lights]

    camera = req.get("camera")
    if isinstance(camera, dict):
        slices = max(1, _int(req.get("depth_slices"), gz))
        region_count = gx * gy * slices
        if not lights or region_count == 0:
            return _finish([[] for _ in range(region_count)], [], region_count,
                           len(lights))
        regions = _frustum_regions(camera, gx, gy, slices)
        clusters, identities = _assign(lights, lambda: regions)
        return _finish(clusters, identities, len(regions), len(lights))

    bounds = req.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 2:
        bounds = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    first = _vec3(bounds[0], (0.0, 0.0, 0.0))
    second = _vec3(bounds[1], (1.0, 1.0, 1.0))
    grid_lo = [min(first[a], second[a]) for a in range(3)]
    grid_hi = [max(first[a], second[a]) for a in range(3)]

    if total == 0 or not lights:
        return _finish([[] for _ in range(total)], [], total, len(lights))

    # The documented contract is a uniform grid over ``bounds``.  Explicit
    # per-axis boundaries are honoured when supplied (e.g. logarithmic depth
    # slices); ``_candidate_span`` handles non-uniform and degenerate edges.
    explicit = req.get("edges")
    edges: List[List[float]] = []
    for axis, count in enumerate(counts_xyz):
        custom: Optional[List[float]] = None
        if isinstance(explicit, (list, tuple)) and len(explicit) == 3:
            candidate = explicit[axis]
            if (isinstance(candidate, (list, tuple))
                    and len(candidate) == count + 1):
                values = [_num(v, grid_lo[axis]) for v in candidate]
                if all(values[i] <= values[i + 1] for i in range(count)):
                    custom = values
        edges.append(custom if custom is not None
                     else _axis_edges(grid_lo[axis], grid_hi[axis], count))

    clusters: List[List[int]] = [[] for _ in range(total)]
    identities: List[str] = []

    xs, ys, zs = edges[0], edges[1], edges[2]

    for light_id, light in enumerate(lights):
        center, radius, _kind, _half, _direction = _influence(light)
        identities.append(_identity(light))

        slack = _REL_SLACK * max(
            1.0, abs(radius),
            abs(center[0]), abs(center[1]), abs(center[2]),
            abs(grid_lo[0]), abs(grid_lo[1]), abs(grid_lo[2]),
            abs(grid_hi[0]), abs(grid_hi[1]), abs(grid_hi[2]),
            grid_hi[0] - grid_lo[0],
            grid_hi[1] - grid_lo[1],
            grid_hi[2] - grid_lo[2],
        )

        # Cheap whole-grid rejection before touching individual cells.
        if not _sphere_hits_box(center, radius, grid_lo, grid_hi, slack):
            continue

        swept = [(center[a] - radius - slack, center[a] + radius + slack)
                 for a in range(3)]
        i0, i1 = _candidate_span(xs, *swept[0])
        j0, j1 = _candidate_span(ys, *swept[1])
        k0, k1 = _candidate_span(zs, *swept[2])
        if i0 > i1 or j0 > j1 or k0 > k1:
            continue

        for k in range(k0, k1 + 1):
            z_lo, z_hi = _axis_bounds(zs, k)
            for j in range(j0, j1 + 1):
                y_lo, y_hi = _axis_bounds(ys, j)
                row = (k * gy + j) * gx
                for i in range(i0, i1 + 1):
                    x_lo, x_hi = _axis_bounds(xs, i)
                    if _sphere_hits_box(center, radius,
                                        (x_lo, y_lo, z_lo),
                                        (x_hi, y_hi, z_hi), slack):
                        clusters[row + i].append(light_id)

    return _finish(clusters, identities, total, len(lights))


def _finish(clusters: List[List[int]], identities: List[str], total: int,
            light_count: int = 0) -> dict:
    counts = [len(ids) for ids in clusters]

    # Order-independent digest over geometry signatures, not over input ids.
    canonical = [sorted(identities[light_id] for light_id in ids)
                 for ids in clusters]
    digest = hashlib.sha1(
        json.dumps(canonical, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    per_light = [0] * light_count
    for ids in clusters:
        for light_id in ids:
            if 0 <= light_id < light_count:
                per_light[light_id] += 1

    total_hits = sum(counts)
    covered = sum(1 for hits in per_light if hits)
    stats = {
        # one entry per cluster, in grid order
        "total_hits": total_hits,
        "clusters": total,
        "empty_clusters": sum(1 for count in counts if count == 0),
        "max_cluster_count": max(counts) if counts else 0,
        # "conflict" = a light shared by more than one cluster
        "lights": light_count,
        "lights_in_no_cluster": light_count - covered,
        "conflicting_lights": sum(1 for hits in per_light if hits > 1),
        "duplicate_hits": total_hits - covered,
        "max_clusters_per_light": max(per_light) if per_light else 0,
    }
    return {
        "clusters": clusters,
        "counts": counts,
        "digest": digest,
        "stats": stats,
        # kept as an alias so callers looking for conflict statistics by that
        # name see the same numbers
        "conflicts": dict(stats),
    }


def main(argv: Sequence[str]) -> int:
    if len(argv) != 3:
        print("usage: python3 cluster.py input.json output.json", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8") as handle:
        request = json.load(handle)
    result = build_clusters(request)
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
