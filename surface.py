"""Adaptive octree isosurface extraction with a conforming tetrahedral mesh.

The implementation has three deterministic stages:

1. Refine octree leaves by a geometric level-set error estimate, then enforce
   the full 2:1 neighbor constraint (face, edge and corner neighbors).
2. Turn the balanced octree into a conforming tetrahedralization.  Coarse
   faces adjacent to finer leaves are triangulated with their edge/face
   midpoints; fine-side faces use the matching diagonal.  Every boundary
   triangle is coned to the leaf center.
3. Run marching tetrahedra on the shared tetrahedral mesh.  Intersection
   vertices are identified by global mesh-edge keys.  Exact zero grid values
   are classified as inside and all intersections at that grid point share one
   vertex.  Triangle winding is oriented by the outward signed-distance
   gradient.

The public output is canonicalized, so leaf insertion, traversal and export
order do not change vertices or triangles.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]
NodeKey = Tuple[int, int, int]
LeafKey = Tuple[int, int, int, int]
VertexKey = tuple


# ---------------------------------------------------------------------------
# Analytic test fields
# ---------------------------------------------------------------------------

def field_value(config: dict, x: float, y: float, z: float) -> float:
    cx, cy, cz = config.get("center", [0.0, 0.0, 0.0])
    x, y, z = x - cx, y - cy, z - cz
    kind = config["field"]
    if kind == "sphere":
        return math.sqrt(x * x + y * y + z * z) - config["radius"]
    if kind == "shell":
        r = math.sqrt(x * x + y * y + z * z)
        return max(r - config["outer"], config["inner"] - r)
    if kind == "torus":
        q = math.hypot(x, y)
        return math.hypot(q - config["major"], z) - config["minor"]
    raise ValueError(f"unknown field: {kind}")


def field_gradient(config: dict, x: float, y: float, z: float) -> Vec3:
    """Outward level-set gradient; finite differences cover custom fields."""
    kind = config["field"]
    if kind not in ("sphere", "shell", "torus"):
        epsilon = 1.0e-7 * max(1.0, abs(x), abs(y), abs(z))
        result = []
        for axis in range(3):
            plus = [x, y, z]
            minus = [x, y, z]
            plus[axis] += epsilon
            minus[axis] -= epsilon
            result.append((field_value(config, *plus) -
                           field_value(config, *minus)) / (2.0 * epsilon))
        return tuple(result)

    cx, cy, cz = config.get("center", [0.0, 0.0, 0.0])
    qx, qy, qz = x - cx, y - cy, z - cz
    if kind == "sphere":
        radius = max(math.hypot(qx, qy, qz), 1.0e-14)
        return (qx / radius, qy / radius, qz / radius)
    if kind == "shell":
        radius = math.hypot(qx, qy, qz)
        sign = 1.0 if radius >= (config["inner"] + config["outer"]) * 0.5 else -1.0
        if radius <= 1.0e-14:
            return (1.0, 0.0, 0.0)
        return (sign * qx / radius, sign * qy / radius, sign * qz / radius)
    if kind == "torus":
        planar = math.hypot(qx, qy)
        if planar <= 1.0e-14:
            return (-1.0, 0.0, 0.0)
        d = math.hypot(planar - config["major"], qz)
        if d <= 1.0e-14:
            return (qx / planar, qy / planar, 0.0)
        return (
            (planar - config["major"]) * qx / (planar * d),
            (planar - config["major"]) * qy / (planar * d),
            qz / d,
        )


def _sample_grid(config: dict, lo: Vec3, h: float) -> List[float]:
    values: List[float] = []
    for ix in range(3):
        for iy in range(3):
            for iz in range(3):
                p = (lo[0] + ix * h / 2.0,
                     lo[1] + iy * h / 2.0,
                     lo[2] + iz * h / 2.0)
                values.append(field_value(config, *p))
    return values


def _subdivision_error(config: dict, lo: Vec3, h: float) -> float:
    """Estimate geometric level-set approximation error in a cube.

    The estimate combines the quadratic Taylor residual at the corners and the
    level-set mean-curvature term.  Both are geometric distances because the
    supplied fields are signed-distance fields (gradient length one away from
    the harmless shell cusp).
    """
    c = (lo[0] + h / 2.0, lo[1] + h / 2.0, lo[2] + h / 2.0)
    d = h / 4.0
    f0 = field_value(config, *c)

    def f_at(i: int, j: int, k: int) -> float:
        return field_value(config, c[0] + i * d, c[1] + j * d, c[2] + k * d)

    g = (
        (f_at(1, 0, 0) - f_at(-1, 0, 0)) / (2 * d),
        (f_at(0, 1, 0) - f_at(0, -1, 0)) / (2 * d),
        (f_at(0, 0, 1) - f_at(0, 0, -1)) / (2 * d),
    )
    gnorm = max(math.sqrt(sum(v * v for v in g)), 1.0e-12)

    h00 = (f_at(1, 0, 0) - 2 * f0 + f_at(-1, 0, 0)) / (d * d)
    h11 = (f_at(0, 1, 0) - 2 * f0 + f_at(0, -1, 0)) / (d * d)
    h22 = (f_at(0, 0, 1) - 2 * f0 + f_at(0, 0, -1)) / (d * d)
    h01 = (f_at(1, 1, 0) - f_at(1, -1, 0)
           - f_at(-1, 1, 0) + f_at(-1, -1, 0)) / (4 * d * d)
    h02 = (f_at(1, 0, 1) - f_at(1, 0, -1)
           - f_at(-1, 0, 1) + f_at(-1, 0, -1)) / (4 * d * d)
    h12 = (f_at(0, 1, 1) - f_at(0, 1, -1)
           - f_at(0, -1, 1) + f_at(0, -1, -1)) / (4 * d * d)
    H = ((h00, h01, h02), (h01, h11, h12), (h02, h12, h22))

    residual = 0.0
    for bx in (0.0, 1.0):
        for by in (0.0, 1.0):
            for bz in (0.0, 1.0):
                delta = ((bx - 0.5) * h, (by - 0.5) * h, (bz - 0.5) * h)
                linear = f0 + sum(g[t] * delta[t] for t in range(3))
                quadratic = 0.5 * sum(
                    H[r][s] * delta[r] * delta[s]
                    for r in range(3) for s in range(3)
                )
                actual = field_value(
                    config, lo[0] + bx * h, lo[1] + by * h, lo[2] + bz * h
                )
                residual = max(residual, abs(actual - linear - quadratic))
    residual /= gnorm

    n = tuple(v / gnorm for v in g)
    trace = h00 + h11 + h22
    normal_second = sum(H[r][s] * n[r] * n[s] for r in range(3) for s in range(3))
    curvature = abs(trace - normal_second) / gnorm
    return max(residual, 0.5 * curvature * h * h)


# ---------------------------------------------------------------------------
# Octree construction and 2:1 balancing
# ---------------------------------------------------------------------------

class Octree:
    def __init__(self, config: dict):
        lo, hi = config["domain"]
        if hi <= lo:
            raise ValueError("domain must be increasing")
        self.config = config
        self.lo = float(lo)
        self.size = float(hi) - self.lo
        self.max_depth = int(config["max_depth"])
        if not 0 <= self.max_depth <= 7:
            raise ValueError("max_depth must be between 0 and 7")
        # One extra level stores leaf centers and finest-edge midpoints.
        self.grid_depth = self.max_depth + 1
        self.leaves: set[LeafKey] = set()

    def bounds(self, leaf: LeafKey) -> Tuple[Vec3, float]:
        d, i, j, k = leaf
        h = self.size / (1 << d)
        return (self.lo + i * h, self.lo + j * h, self.lo + k * h), h

    def occupant(self, depth: int, i: int, j: int, k: int) -> LeafKey | None:
        """Return the leaf containing a depth-``depth`` octant."""
        n = 1 << depth
        if not (0 <= i < n and 0 <= j < n and 0 <= k < n):
            return None
        for q in range(depth, -1, -1):
            shift = depth - q
            key = (q, i >> shift, j >> shift, k >> shift)
            if key in self.leaves:
                return key
        return None

    def build(self) -> None:
        self._refine((0, 0, 0, 0))
        self._balance(int(self.config.get("seed", 0)))

    def _refine(self, leaf: LeafKey) -> None:
        d, i, j, k = leaf
        lo, h = self.bounds(leaf)
        values = _sample_grid(self.config, lo, h)
        diagonal = h * math.sqrt(3.0)
        near_surface = min(abs(v) for v in values) <= diagonal + 1.0e-12
        if not near_surface or d == self.max_depth:
            self.leaves.add(leaf)
            return
        error = _subdivision_error(self.config, lo, h)
        if error <= float(self.config["tolerance"]):
            self.leaves.add(leaf)
            return
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    self._refine((d + 1, 2 * i + dx, 2 * j + dy, 2 * k + dz))

    def _subdivide(self, leaf: LeafKey) -> List[LeafKey]:
        if leaf not in self.leaves:
            return []
        d, i, j, k = leaf
        self.leaves.remove(leaf)
        children = []
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    child = (d + 1, 2 * i + dx, 2 * j + dy, 2 * k + dz)
                    self.leaves.add(child)
                    children.append(child)
        return children

    def _balance(self, seed: int) -> None:
        rng = random.Random(seed)
        queue = list(self.leaves)
        rng.shuffle(queue)
        while queue:
            leaf = queue.pop()
            d, i, j, k = leaf
            if leaf not in self.leaves:
                continue
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for dk in (-1, 0, 1):
                        if di == dj == dk == 0:
                            continue
                        other = self.occupant(d, i + di, j + dj, k + dk)
                        if other is not None and other[0] <= d - 2:
                            queue.extend(self._subdivide(other))
        # The closure above is order independent; assert the invariant once.
        for leaf in list(self.leaves):
            d, i, j, k = leaf
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for dk in (-1, 0, 1):
                        if di == dj == dk == 0:
                            continue
                        other = self.occupant(d, i + di, j + dj, k + dk)
                        assert other is None or abs(other[0] - d) <= 1


# ---------------------------------------------------------------------------
# Conforming tetrahedralization of a balanced octree
# ---------------------------------------------------------------------------

class TetraMesh:
    def __init__(self, tree: Octree):
        self.tree = tree
        self.g = tree.grid_depth
        self.tetrahedra: List[Tuple[NodeKey, NodeKey, NodeKey, NodeKey]] = []

    def node_coordinate(self, key: NodeKey) -> Vec3:
        scale = self.tree.size / (1 << self.g)
        return (self.tree.lo + key[0] * scale,
                self.tree.lo + key[1] * scale,
                self.tree.lo + key[2] * scale)

    def _corner(self, leaf: LeafKey, bits: Tuple[int, int, int]) -> NodeKey:
        d, i, j, k = leaf
        step = 1 << (self.g - d)
        base = (i * step, j * step, k * step)
        return (base[0] + bits[0] * step,
                base[1] + bits[1] * step,
                base[2] + bits[2] * step)

    def _edge_is_split(self, leaf: LeafKey, a: NodeKey, b: NodeKey) -> bool:
        """A cube edge is split iff a depth d+1 leaf touches its midpoint."""
        d = leaf[0]
        mid = tuple((a[t] + b[t]) // 2 for t in range(3))
        shift = self.g - (d + 1)
        p = tuple(v >> shift for v in mid)
        for ox in (0, 1):
            for oy in (0, 1):
                for oz in (0, 1):
                    cell = (p[0] + ox - 1, p[1] + oy - 1, p[2] + oz - 1)
                    occ = self.tree.occupant(d + 1, *cell)
                    if occ is not None and occ[0] == d + 1:
                        return True
        return False

    def _face_corners(self, leaf: LeafKey, axis: int) -> List[List[NodeKey]]:
        others = [x for x in range(3) if x != axis]
        u, v = others
        result = []
        for side in (0, 1):
            corners = []
            for bu, bv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                bits = [0, 0, 0]
                bits[axis] = side
                bits[u], bits[v] = bu, bv
                corners.append(self._corner(leaf, (bits[0], bits[1], bits[2])))
            result.append(corners)
        return result

    def _face_relation(self, leaf: LeafKey, axis: int, side: int) -> str:
        d, i, j, k = leaf
        origin = [i, j, k]
        origin[axis] += 1 if side else -1
        if origin[axis] < 0 or origin[axis] >= (1 << d):
            return "boundary"
        occ = self.tree.occupant(d, *origin)
        if occ is None:
            return "fine"
        if occ[0] < d:
            return "coarse"
        return "same"

    def _triangulate_face(self, leaf: LeafKey, axis: int,
                          side: int) -> List[Tuple[NodeKey, NodeKey, NodeKey]]:
        corners = self._face_corners(leaf, axis)[side]
        relation = self._face_relation(leaf, axis, side)
        u, v = [x for x in range(3) if x != axis]

        if relation == "coarse":
            # This fine square is one quadrant of the coarse neighbor face.
            # The diagonal from the coarse cube corner to the coarse face
            # center exactly matches two coarse-face triangles.
            origin_bits = [leaf[1], leaf[2], leaf[3]]
            wanted_u = origin_bits[u] & 1
            wanted_v = origin_bits[v] & 1
            quadrants = ((0, 0), (1, 0), (1, 1), (0, 1))
            coarse_index = next(
                q for q, (bu, bv) in enumerate(quadrants)
                if bu == wanted_u and bv == wanted_v
            )
            opposite = (coarse_index + 2) % 4
            return [
                (corners[coarse_index],
                 corners[(coarse_index + 1) % 4], corners[opposite]),
                (corners[coarse_index], corners[opposite],
                 corners[(coarse_index + 3) % 4]),
            ]

        split = [
            self._edge_is_split(leaf, corners[a], corners[b])
            for a, b in ((0, 1), (1, 2), (2, 3), (3, 0))
        ]
        if not any(split):
            return [(corners[0], corners[1], corners[2]),
                    (corners[0], corners[2], corners[3])]

        center = tuple((corners[0][t] + corners[2][t]) // 2
                       for t in range(3))
        boundary: List[NodeKey] = []
        for edge, (a, b) in enumerate(((0, 1), (1, 2), (2, 3), (3, 0))):
            boundary.append(corners[a])
            if split[edge]:
                boundary.append(tuple((corners[a][t] + corners[b][t]) // 2
                                      for t in range(3)))
        return [(center, boundary[q], boundary[(q + 1) % len(boundary)])
                for q in range(len(boundary))]

    def build(self, seed: int = 0) -> None:
        rng = random.Random(seed + 0x9e3779b9)
        leaves = list(self.tree.leaves)
        rng.shuffle(leaves)
        for leaf in leaves:
            d, i, j, k = leaf
            step = 1 << (self.g - d)
            base = (i * step, j * step, k * step)
            center = tuple(base[t] + step // 2 for t in range(3))
            for axis in range(3):
                for side in (0, 1):
                    for triangle in self._triangulate_face(leaf, axis, side):
                        self.tetrahedra.append((center, *triangle))


# ---------------------------------------------------------------------------
# Marching tetrahedra
# ---------------------------------------------------------------------------

def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def extract_surface(config: dict, tree: Octree,
                    mesh: TetraMesh) -> dict:
    rng = random.Random(int(config.get("seed", 0)) + 0x12345)
    tets = list(mesh.tetrahedra)
    rng.shuffle(tets)

    field_cache: Dict[NodeKey, float] = {}
    coordinate_cache: Dict[NodeKey, Vec3] = {}
    vertex_coordinate: Dict[VertexKey, Vec3] = {}

    def coordinate(node: NodeKey) -> Vec3:
        if node not in coordinate_cache:
            coordinate_cache[node] = mesh.node_coordinate(node)
        return coordinate_cache[node]

    def scalar(node: NodeKey) -> float:
        if node not in field_cache:
            field_cache[node] = field_value(config, *coordinate(node))
        return field_cache[node]

    def intersection(u: NodeKey, v: NodeKey) -> VertexKey | None:
        fu, fv = scalar(u), scalar(v)
        # Exact zero is inside; collapse all incident crossings to the node.
        if fu == 0.0:
            key: VertexKey = ("n", u)
        elif fv == 0.0:
            key = ("n", v)
        else:
            if fu * fv > 0.0:
                return None
            key = ("e", tuple(sorted((u, v))))
        if key not in vertex_coordinate:
            if key[0] == "n":
                vertex_coordinate[key] = coordinate(key[1])
            else:
                a, b = key[1]
                fa, fb = scalar(a), scalar(b)
                t = fa / (fa - fb)
                pa, pb = coordinate(a), coordinate(b)
                vertex_coordinate[key] = tuple(
                    pa[q] + t * (pb[q] - pa[q]) for q in range(3)
                )
        return key

    raw_faces: set[Tuple[VertexKey, VertexKey, VertexKey]] = set()

    def add_triangle(keys: Sequence[VertexKey],
                     nodes: Sequence[NodeKey], inside: Sequence[int]) -> None:
        if len(set(keys)) != 3:
            return
        points = [vertex_coordinate[key] for key in keys]
        normal = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
        if _norm(normal) <= 1.0e-14:
            return
        centroid = _scale_tuple(tuple(points[0][t] + points[1][t] + points[2][t]
                                      for t in range(3)), 1.0 / 3.0)
        gradient = field_gradient(config, *centroid)
        if _norm(gradient) > 1.0e-12:
            # A level-set triangle is outward if its geometric normal has a
            # positive component along the signed-distance gradient.
            if _dot(normal, gradient) < 0.0:
                keys = (keys[0], keys[2], keys[1])
        else:
            inside_point = None
            for idx in inside:
                p = coordinate(nodes[idx])
                if p != centroid:
                    inside_point = p
                    break
            if inside_point is None:
                return
            if _dot(normal, _sub(inside_point, centroid)) > 0.0:
                keys = (keys[0], keys[2], keys[1])
        q = min(range(3), key=lambda i: keys[i])
        canonical = (keys[q], keys[(q + 1) % 3], keys[(q + 2) % 3])
        raw_faces.add(canonical)

    for tet in tets:
        nodes = list(tet)
        values = [scalar(node) for node in nodes]
        inside = [i for i, value in enumerate(values) if value <= 0.0]
        outside = [i for i, value in enumerate(values) if value > 0.0]
        if not inside or not outside:
            continue
        inside.sort(key=lambda i: nodes[i])
        outside.sort(key=lambda i: nodes[i])

        if len(inside) == 1:
            i = inside[0]
            points = [intersection(nodes[i], nodes[o]) for o in outside]
            if all(points):
                add_triangle(points, nodes, inside)
        elif len(outside) == 1:
            o = outside[0]
            points = [intersection(nodes[i], nodes[o]) for i in inside]
            if all(points):
                add_triangle(points, nodes, inside)
        elif len(inside) == 2 and len(outside) == 2:
            i0, i1 = inside
            o0, o1 = outside
            a = intersection(nodes[i0], nodes[o0])
            b = intersection(nodes[i0], nodes[o1])
            c = intersection(nodes[i1], nodes[o1])
            d = intersection(nodes[i1], nodes[o0])
            if None not in (a, b, c, d):
                add_triangle((a, b, c), nodes, inside)
                add_triangle((a, c, d), nodes, inside)

    # Canonical coordinates and face order.  This removes any dependence on
    # randomized octree traversal or dictionary/object insertion order.
    vertex_keys = sorted({key for face in raw_faces for key in face},
                         key=lambda key: vertex_coordinate[key])
    vertex_ids = {key: i for i, key in enumerate(vertex_keys)}
    vertices = [list(vertex_coordinate[key]) for key in vertex_keys]

    face_ids: set[Tuple[int, int, int]] = set()
    for face in raw_faces:
        ids = [vertex_ids[key] for key in face]
        q = min(range(3), key=lambda i: ids[i])
        rotated = (ids[q], ids[(q + 1) % 3], ids[(q + 2) % 3])
        face_ids.add(rotated)
    faces = [list(face) for face in sorted(face_ids)]

    depths: Dict[str, int] = {}
    for leaf in tree.leaves:
        key = str(leaf[0])
        depths[key] = depths.get(key, 0) + 1
    return {
        "vertices": vertices,
        "faces": faces,
        "stats": {
            "leaf_depths": dict(sorted(depths.items(), key=lambda x: int(x[0]))),
            "num_leaves": len(tree.leaves),
            "num_tetrahedra": len(mesh.tetrahedra),
        },
    }


def _scale_tuple(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def extract(config: dict) -> dict:
    tree = Octree(config)
    tree.build()
    mesh = TetraMesh(tree)
    mesh.build(seed=int(config.get("seed", 0)))
    return extract_surface(config, tree, mesh)


def write_obj(mesh: dict, path: Path) -> None:
    lines = ["# Adaptive octree isosurface"]
    for x, y, z in mesh["vertices"]:
        lines.append(f"v {x:.12g} {y:.12g} {z:.12g}")
    for a, b, c in mesh["faces"]:
        lines.append(f"f {a + 1} {b + 1} {c + 1}")
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    mesh = extract(config)
    if args.output.suffix.lower() == ".obj":
        write_obj(mesh, args.output)
    else:
        args.output.write_text(json.dumps(mesh, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
