"""Manifold triangle-mesh slicing with closed, capped section geometry."""
import argparse
import json
import math
from pathlib import Path


class SliceError(ValueError):
    """A mesh error carrying enough information to locate a face/edge/vertex."""

    def __init__(self, message, location=None):
        super().__init__(message)
        self.location = location or {}

    def __str__(self):
        if not self.location:
            return super().__str__()
        loc = json.dumps(self.location, ensure_ascii=False, sort_keys=True)
        return f"{super().__str__()} | location={loc}"


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------

def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _scale(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _normalize(a):
    length = _norm(a)
    if length == 0.0 or not math.isfinite(length):
        raise SliceError("invalid plane normal")
    return [x / length for x in a]


def _signed_volume(vertices, faces):
    total = 0.0
    for face in faces:
        a, b, c = (vertices[i] for i in face["indices"])
        total += _dot(a, _cross(b, c)) / 6.0
    return total

# ---------------------------------------------------------------------------
# Input validation and consistent outward orientation
# ---------------------------------------------------------------------------

def _weld_vertices(vertices, eps):
    if not vertices:
        return [], []
    if any(len(v) != 3 or not all(math.isfinite(x) for x in v) for v in vertices):
        raise SliceError("invalid vertex: each vertex must contain three finite numbers")

    step = max(eps, 1e-300)
    grid = {}
    welded = []
    remap = []
    for point in vertices:
        cell = tuple(int(math.floor(x / step)) for x in point)
        match = None
        for ix in range(cell[0] - 1, cell[0] + 2):
            for iy in range(cell[1] - 1, cell[1] + 2):
                for iz in range(cell[2] - 1, cell[2] + 2):
                    for candidate in grid.get((ix, iy, iz), ()):
                        if _norm(_sub(point, welded[candidate])) <= eps:
                            match = candidate
                            break
                    if match is not None:
                        break
                if match is not None:
                    break
            if match is not None:
                break
        if match is None:
            match = len(welded)
            welded.append([float(x) for x in point])
            grid.setdefault(cell, []).append(match)
        remap.append(match)
    return welded, remap


def _valid_uv(uv):
    return (
        uv is not None
        and len(uv) == 3
        and all(
            len(pair) == 2 and all(math.isfinite(float(x)) for x in pair)
            for pair in uv
        )
    )


def prepare_mesh(mesh):
    """Validate/weld input and return outward-oriented closed components."""
    if not isinstance(mesh, dict) or "vertices" not in mesh or "faces" not in mesh:
        raise SliceError("mesh must contain vertices and faces")
    raw_vertices = mesh["vertices"]
    raw_faces = mesh["faces"]
    if not isinstance(raw_vertices, list) or not isinstance(raw_faces, list):
        raise SliceError("vertices and faces must be lists")

    scale = max([1.0] + [
        _norm(v) for v in raw_vertices if isinstance(v, (list, tuple)) and len(v) == 3
    ])
    eps = 1e-9 * scale
    area_eps = 1e-12 * scale * scale
    vertices, remap = _weld_vertices(raw_vertices, eps)

    face_records = []
    seen_triangles = set()
    for original_face, face in enumerate(raw_faces):
        if not isinstance(face, dict) or "indices" not in face:
            raise SliceError("invalid face", {"face": original_face})
        raw_indices = face["indices"]
        if (
            len(raw_indices) != 3
            or any(not isinstance(i, int) or not 0 <= i < len(raw_vertices) for i in raw_indices)
        ):
            raise SliceError("invalid triangle index", {"face": original_face})

        ids = tuple(remap[i] for i in raw_indices)
        uv = face.get("uv")
        if uv is not None and not _valid_uv(uv):
            raise SliceError(
                "invalid face UV; expected three finite 2D coordinates",
                {"face": original_face},
            )

        # Repeated/collinear and exact duplicate triangles are removed rather
        # than producing zero-area or redundant cut fragments.
        if len(set(ids)) != 3:
            continue
        a, b, c = (vertices[i] for i in ids)
        if _norm(_cross(_sub(b, a), _sub(c, a))) <= area_eps:
            continue
        signature = frozenset(ids)
        if signature in seen_triangles:
            continue
        seen_triangles.add(signature)
        face_records.append({
            "ids": ids,
            "uv": uv,
            "material": face.get("material", "surface"),
            "original_face": original_face,
        })

    if not face_records:
        raise SliceError("mesh contains no non-degenerate triangles")

    edge_faces = {}
    for fi, record in enumerate(face_records):
        ids = record["ids"]
        for k in range(3):
            edge = tuple(sorted((ids[k], ids[(k + 1) % 3])))
            edge_faces.setdefault(edge, []).append(fi)

    for edge, incident in edge_faces.items():
        if len(incident) > 2:
            raise SliceError(
                "non-manifold edge is incident to more than two faces",
                {
                    "edge": list(edge),
                    "vertices": [vertices[edge[0]], vertices[edge[1]]],
                    "faces": [face_records[i]["original_face"] for i in incident],
                },
            )
    for edge, incident in edge_faces.items():
        if len(incident) == 1:
            raise SliceError(
                "open mesh edge; a closed manifold input is required",
                {
                    "edge": list(edge),
                    "vertices": [vertices[edge[0]], vertices[edge[1]]],
                    "face": face_records[incident[0]]["original_face"],
                },
            )

    adjacency = [[] for _ in face_records]
    for edge, incident in edge_faces.items():
        a, b = incident
        adjacency[a].append((b, edge))
        adjacency[b].append((a, edge))

    oriented = [None] * len(face_records)
    components = []
    for seed in range(len(face_records)):
        if oriented[seed] is not None:
            continue
        oriented[seed] = face_records[seed]["ids"]
        queue = [seed]
        component = []
        while queue:
            fi = queue.pop()
            component.append(fi)
            current = oriented[fi]
            current_directed = set(zip(current, current[1:] + current[:1]))
            for neighbor, edge in adjacency[fi]:
                x, y = edge
                neighbor_ids = face_records[neighbor]["ids"]
                neighbor_directed = set(zip(neighbor_ids, neighbor_ids[1:] + neighbor_ids[:1]))
                # A consistently oriented neighbor traverses the shared edge
                # in the opposite direction.
                flip = ((x, y) in current_directed) == ((x, y) in neighbor_directed)
                neighbor_orientation = (
                    tuple(reversed(neighbor_ids)) if flip else neighbor_ids
                )
                if oriented[neighbor] is None:
                    oriented[neighbor] = neighbor_orientation
                    queue.append(neighbor)
                elif oriented[neighbor] != neighbor_orientation:
                    raise SliceError(
                        "non-orientable face adjacency",
                        {
                            "edge": list(edge),
                            "faces": [
                                face_records[fi]["original_face"],
                                face_records[neighbor]["original_face"],
                            ],
                        },
                    )
        components.append(component)

    final_faces = [None] * len(face_records)
    volume_eps = 1e-12 * scale ** 3
    for component in components:
        temporary = []
        for fi in component:
            record = face_records[fi]
            ids = oriented[fi]
            if record["uv"] is None:
                corners = [(i, None) for i in ids]
            else:
                original_ids = record["ids"]
                corners = [
                    (i, record["uv"][original_ids.index(i)])
                    for i in ids
                ]
            temporary.append({
                "indices": ids,
                "corners": corners,
                "material": record["material"],
                "original_face": record["original_face"],
            })

        # BFS gives a consistent relative winding; component volume selects
        # whether that common winding is outward or inward.
        if abs(_signed_volume(vertices, temporary)) <= volume_eps:
            raise SliceError(
                "component has zero signed volume",
                {"faces": [face_records[fi]["original_face"] for fi in component]},
            )
        if _signed_volume(vertices, temporary) < 0:
            for face in temporary:
                face["indices"] = tuple(reversed(face["indices"]))
                face["corners"].reverse()
        for fi, face in zip(component, temporary):
            final_faces[fi] = face

    return vertices, final_faces, components, eps


# ---------------------------------------------------------------------------
# Triangle splitting with shared intersection identities
# ---------------------------------------------------------------------------

class VertexBuilder:
    def __init__(self, vertices):
        self.vertices = [list(v) for v in vertices]
        self.edge_cache = {}

    def add(self, point):
        self.vertices.append([float(point[0]), float(point[1]), float(point[2])])
        return len(self.vertices) - 1

    def edge_intersection(self, a, b, distances, normal, offset):
        if distances[a] == 0:
            return a
        if distances[b] == 0:
            return b
        key = tuple(sorted((a, b)))
        if key in self.edge_cache:
            return self.edge_cache[key]

        da, db = distances[a], distances[b]
        t = min(1.0, max(0.0, da / (da - db)))
        point = _add(self.vertices[a], _scale(_sub(self.vertices[b], self.vertices[a]), t))
        # Project away only the cut-plane residual, preserving in-plane position.
        residual = _dot(point, normal) - offset
        point = _sub(point, _scale(normal, residual))
        new_id = self.add(point)
        self.edge_cache[key] = new_id
        return new_id


def _interpolate_uv(uv_a, uv_b, t):
    if uv_a is None or uv_b is None:
        return None
    return [
        uv_a[0] + t * (uv_b[0] - uv_a[0]),
        uv_a[1] + t * (uv_b[1] - uv_a[1]),
    ]


def _clip_polygon(corners, distances, builder, normal, offset, keep_positive):
    def inside(distance):
        return distance >= 0 if keep_positive else distance <= 0

    result = []
    for start, end in zip(corners, corners[1:] + corners[:1]):
        ds, de = distances[start[0]], distances[end[0]]
        if inside(de):
            if not inside(ds):
                t = ds / (ds - de)
                new_id = builder.edge_intersection(start[0], end[0], distances, normal, offset)
                result.append((new_id, _interpolate_uv(start[1], end[1], t)))
            result.append(end)
        elif inside(ds):
            t = ds / (ds - de)
            new_id = builder.edge_intersection(start[0], end[0], distances, normal, offset)
            result.append((new_id, _interpolate_uv(start[1], end[1], t)))

    clean = []
    for corner in result:
        if not clean or clean[-1][0] != corner[0]:
            clean.append(corner)
    if len(clean) > 1 and clean[0][0] == clean[-1][0]:
        clean.pop()
    return clean


# ---------------------------------------------------------------------------
# Section-loop recovery, hole bridging, and triangulation
# ---------------------------------------------------------------------------

def _plane_basis(normal):
    reference = min(
        ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        key=lambda axis: abs(_dot(normal, axis)),
    )
    u = _sub(reference, _scale(normal, _dot(reference, normal)))
    u = _normalize(u)
    v = _cross(normal, u)  # u x v == normal
    return u, v


def _polygon_area_2d(points, loop):
    area = 0.0
    for a_id, b_id in zip(loop, loop[1:] + loop[:1]):
        a, b = points[a_id], points[b_id]
        area += a[0] * b[1] - b[0] * a[1]
    return area * 0.5


def _on_segment_2d(p, a, b, eps):
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (p[0] - a[0]) * (b[1] - a[1])
    if abs(cross) > eps:
        return False
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )


def _point_in_loop(p, loop, points, eps):
    inside = False
    x, y = p
    for a_id, b_id in zip(loop, loop[1:] + loop[:1]):
        a, b = points[a_id], points[b_id]
        if _on_segment_2d(p, a, b, eps):
            return True
        ax, ay = a
        bx, by = b
        if (ay > y) != (by > y):
            crossing_x = ax + (y - ay) * (bx - ax) / (by - ay)
            if crossing_x > x:
                inside = not inside
    return inside


def _material_center(outer, holes, points, area_eps, location):
    for index, point_id in enumerate(outer):
        following = outer[(index + 1) % len(outer)]
        previous = outer[(index - 1) % len(outer)]
        candidate = [
            (points[point_id][0] + points[following][0] + points[previous][0]) / 3,
            (points[point_id][1] + points[following][1] + points[previous][1]) / 3,
        ]
        if _point_in_loop(candidate, outer, points, area_eps) and not any(
            _point_in_loop(candidate, hole, points, area_eps) for hole in holes
        ):
            return candidate
    raise SliceError("could not find material interior point for section cap", location)


def _triangulate(polygon, points, outer, holes, area_eps, location,
                 vertices=None, uv_points=None, uv_u=None, uv_v=None, origin=None,
                 basis_u=None, basis_v=None):
    def star_is_valid(center):
        for x, y in zip(outer, outer[1:] + outer[:1]):
            a, b = points[x], points[y]
            cross = (b[0] - a[0]) * (center[1] - a[1]) - \
                (b[1] - a[1]) * (center[0] - a[0])
            if abs(cross) <= area_eps:
                return False
        for hole in holes:
            for x, y in zip(hole, hole[1:] + hole[:1]):
                a, b = points[x], points[y]
                cross = (b[0] - a[0]) * (center[1] - a[1]) - \
                    (b[1] - a[1]) * (center[0] - a[0])
                if abs(cross) <= area_eps:
                    return False
        return True

    center2 = _material_center(outer, holes, points, area_eps, location)
    if holes or not star_is_valid(center2):
        candidates = []
        if not holes:
            candidates.append(center2)
            for point_id in outer:
                candidates.append(points[point_id])
            extent = max(1.0, max(abs(center2[0]), abs(center2[1])))
            step = math.sqrt(area_eps) * max(1.0, extent)
            for shift_x in range(-4, 5):
                for shift_y in range(-4, 5):
                    if shift_x == 0 and shift_y == 0:
                        continue
                    candidates.append([
                        center2[0] + shift_x * step,
                        center2[1] + shift_y * step,
                    ])
        else:
            for outer_id in outer:
                for hole in holes:
                    for hole_id in hole:
                        p, q = points[outer_id], points[hole_id]
                        midpoint = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]
                        candidates.extend([
                            midpoint,
                            [0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]],
                            [0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]],
                        ])
        center2 = next(
            (candidate for candidate in candidates
             if _point_in_loop(candidate, outer, points, area_eps)
             and not any(_point_in_loop(candidate, hole, points, area_eps) for hole in holes)
             and star_is_valid(candidate)),
            None,
        )
        if center2 is None:
            raise SliceError("could not construct nondegenerate section cap center", location)

    result = []

    def emit_star(loop, center, filter_material):
        center_id = len(vertices)
        center3 = [
            origin[0] + center[0] * basis_u[0] + center[1] * basis_v[0],
            origin[1] + center[0] * basis_u[1] + center[1] * basis_v[1],
            origin[2] + center[0] * basis_u[2] + center[1] * basis_v[2],
        ]
        vertices.append(center3)
        points[center_id] = center
        uv_points[center_id] = (
            _dot(center3, uv_u) - _dot(origin, uv_u),
            _dot(center3, uv_v) - _dot(origin, uv_v),
        )
        for x, y in zip(loop, loop[1:] + loop[:1]):
            a, b = points[x], points[y]
            cross = (b[0] - a[0]) * (center[1] - a[1]) - \
                (b[1] - a[1]) * (center[0] - a[0])
            if abs(cross) <= area_eps:
                continue
            centroid = [(a[0] + b[0] + center[0]) / 3,
                        (a[1] + b[1] + center[1]) / 3]
            if filter_material and any(
                _point_in_loop(centroid, hole, points, area_eps) for hole in holes
            ):
                continue
            result.append(tuple(reversed((x, y, center_id))))

    def valid_loop_center(center, loop):
        return all(
            abs(
                (points[y][0] - points[x][0]) * (center[1] - points[x][1])
                - (points[y][1] - points[x][1]) * (center[0] - points[x][0])
            ) > area_eps
            for x, y in zip(loop, loop[1:] + loop[:1])
        )

    def interior_loop_center(loop):
        base = [
            sum(points[vid][0] for vid in loop) / len(loop),
            sum(points[vid][1] for vid in loop) / len(loop),
        ]
        if valid_loop_center(base, loop):
            return base
        extent = max(1.0, max(abs(base[0]), abs(base[1])))
        step = math.sqrt(area_eps) * max(1.0, extent)
        for ix in range(-8, 9):
            for iy in range(-8, 9):
                candidate = [base[0] + ix * step, base[1] + iy * step]
                if _point_in_loop(candidate, loop, points, area_eps) and \
                        valid_loop_center(candidate, loop):
                    return candidate
        raise SliceError("could not construct section hole center", location)

    emit_star(outer, center2, True)
    for hole in holes:
        emit_star(hole, interior_loop_center(hole), False)
    if not result:
        raise SliceError("degenerate final section triangle", location)
    return result


def _cap_side(side_faces, vertices, distances, plane_normal, offset, side_name, eps):
    edge_info = {}
    for face in side_faces:
        ids = face["indices"]
        for a, b in zip(ids, ids[1:] + ids[:1]):
            edge_info.setdefault(tuple(sorted((a, b))), []).append((a, b))

    for edge, directed_edges in edge_info.items():
        if len(directed_edges) > 2 or (
            len(directed_edges) == 2 and directed_edges[0] == directed_edges[1]
        ):
            raise SliceError(
                "non-manifold edge created while slicing",
                {"side": side_name, "edge": list(edge),
                 "vertices": [vertices[edge[0]], vertices[edge[1]]]},
            )

    boundary_edges = [entries[0] for entries in edge_info.values() if len(entries) == 1]
    if not boundary_edges:
        return

    # The cap outward normal points away from the retained half-space.
    cap_normal = list(plane_normal) if side_name == "negative" else [-x for x in plane_normal]
    u, v = _plane_basis(plane_normal)
    uv_u, uv_v = _plane_basis(plane_normal)
    origin = _scale(plane_normal, offset)
    points2 = {}
    uv_points = {}

    def planar_point(vid):
        if vid not in points2:
            point = vertices[vid]
            points2[vid] = (
                _dot(point, u) - _dot(origin, u),
                _dot(point, v) - _dot(origin, v),
            )
            uv_points[vid] = (
                _dot(point, uv_u) - _dot(origin, uv_u),
                _dot(point, uv_v) - _dot(origin, uv_v),
            )
        return points2[vid]

    for a, b in boundary_edges:
        if abs(distances[a]) > eps or abs(distances[b]) > eps:
            raise SliceError(
                "sliced boundary is not contained in the cut plane",
                {"side": side_name, "edge": [a, b]},
            )
        planar_point(a)
        planar_point(b)

    outgoing = {}
    for a, b in boundary_edges:
        outgoing.setdefault(a, []).append(b)

    used = set()
    loops = []
    for start_a, start_b in boundary_edges:
        if (start_a, start_b) in used:
            continue
        loop = [start_a]
        previous, current = start_a, start_b
        while current != start_a:
            used.add((previous, current))
            candidates = [nxt for nxt in outgoing.get(current, ()) if (current, nxt) not in used]
            if not candidates:
                raise SliceError(
                    "section boundary does not form a closed loop",
                    {"side": side_name, "vertex": current, "position": vertices[current]},
                )
            if len(candidates) == 1:
                nxt = candidates[0]
            else:
                # At a tangent/contact vertex, choose the smallest turning
                # angle, preserving each local boundary sheet rather than
                # jumping between different rings.
                incoming = _sub(vertices[current], vertices[previous])
                base_angle = math.atan2(incoming[1], incoming[0])

                def turn_angle(target):
                    outgoing_vector = _sub(vertices[target], vertices[current])
                    delta = math.atan2(outgoing_vector[1], outgoing_vector[0]) - base_angle
                    return abs((delta + math.pi) % (2 * math.pi) - math.pi)

                nxt = min(candidates, key=turn_angle)
            loop.append(current)
            previous, current = current, nxt
        used.add((previous, current))
        if len(loop) >= 3:
            loops.append(loop)

    if len(used) != len(boundary_edges):
        raise SliceError("failed to recover all section boundary loops", {"side": side_name})

    area_eps = eps * eps
    outer_loops, hole_loops = [], []
    for loop in loops:
        center = [
            sum(points2[i][0] for i in loop) / len(loop),
            sum(points2[i][1] for i in loop) / len(loop),
        ]
        enclosing = [
            other for other in loops
            if other is not loop
            and abs(_polygon_area_2d(points2, other)) > abs(_polygon_area_2d(points2, loop))
            and _point_in_loop(center, other, points2, area_eps)
        ]
        if enclosing:
            hole_loops.append(loop)
        else:
            outer_loops.append(loop)
    for loop in loops:
        area = _polygon_area_2d(points2, loop)
        if abs(area) <= area_eps:
            raise SliceError(
                "zero-area section loop",
                {"side": side_name, "vertices": loop[:8]},
            )

    grouped = {id(outer): {"outer": outer, "holes": []} for outer in outer_loops}
    for hole in hole_loops:
        center = [
            sum(points2[i][0] for i in hole) / len(hole),
            sum(points2[i][1] for i in hole) / len(hole),
        ]
        containers = [
            outer for outer in outer_loops
            if _point_in_loop(center, outer, points2, area_eps)
        ]
        if not containers:
            raise SliceError(
                "section hole has no enclosing outer loop",
                {"side": side_name, "hole_vertices": hole[:8]},
            )
        # Smallest containing area handles separate/nested outer loops.
        outer = min(containers, key=lambda loop: abs(_polygon_area_2d(points2, loop)))
        grouped[id(outer)]["holes"].append(hole)

    for group in grouped.values():
        outer, holes = group["outer"], group["holes"]
        triangles = _triangulate(
            outer, points2, outer, holes, area_eps,
            {"side": side_name, "outer_vertices": outer[:8]},
            vertices, uv_points, uv_u, uv_v, origin, u, v,
        )
        for tri in triangles:
            side_faces.append({
                "indices": tri,
                "material": "section",
                "uv": [list(uv_points[i]) for i in tri],
                "normal": list(cap_normal),
            })


# ---------------------------------------------------------------------------
# Public slicing operation and output cleanup
# ---------------------------------------------------------------------------

def _compact_mesh(vertices, faces):
    used = sorted({i for face in faces for i in face["indices"]})
    remap = {old: new for new, old in enumerate(used)}
    compact_vertices = [vertices[i] for i in used]
    compact_faces = []
    seen = set()

    for face in faces:
        ids = tuple(remap[i] for i in face["indices"])
        if len(set(ids)) != 3 or frozenset(ids) in seen:
            continue
        p0, p1, p2 = (compact_vertices[ids[0]], compact_vertices[ids[1]], compact_vertices[ids[2]])
        if _norm(_cross(_sub(p1, p0), _sub(p2, p0))) == 0.0:
            continue
        seen.add(frozenset(ids))
        compact_face = {
            "indices": list(ids),
            "material": face.get("material", "surface"),
        }
        if face.get("uv") is not None:
            compact_face["uv"] = face["uv"]
        if face.get("normal") is not None:
            compact_face["normal"] = face["normal"]
        compact_faces.append(compact_face)

    return {"vertices": compact_vertices, "faces": compact_faces}


def _assert_closed(vertices, faces, side_name):
    edge_info = {}
    for face in faces:
        ids = face["indices"]
        for a, b in zip(ids, ids[1:] + ids[:1]):
            edge_info.setdefault(tuple(sorted((a, b))), []).append((a, b))
    for edge, directed in edge_info.items():
        if len(directed) != 2 or directed[0] == directed[1]:
            raise SliceError(
                "sliced result is not closed",
                {"side": side_name, "edge": list(edge),
                 "vertices": [vertices[edge[0]], vertices[edge[1]]]},
            )


def slice_mesh(mesh, plane):
    raw_normal = plane["normal"]
    if len(raw_normal) != 3:
        raise SliceError("invalid plane normal")
    normal = _normalize(raw_normal)
    normal_length = _norm(raw_normal)
    offset = float(plane["offset"]) / normal_length
    if not math.isfinite(offset):
        raise SliceError("invalid plane offset")

    vertices, faces, components, eps = prepare_mesh(mesh)
    builder = VertexBuilder(vertices)

    def signed_distance(point):
        value = _dot(point, normal) - offset
        return 0 if abs(value) <= eps else value

    distances = [signed_distance(p) for p in builder.vertices]
    sides = {"positive": [], "negative": []}

    for component in components:
        component_vertices = {
            vertex_id for fi in component for vertex_id in faces[fi]["indices"]
        }
        has_positive = any(distances[i] > 0 for i in component_vertices)
        has_negative = any(distances[i] < 0 for i in component_vertices)

        if not has_positive and not has_negative:
            raise SliceError(
                "component lies entirely in the cut plane and has no retained volume",
                {"faces": [faces[fi]["original_face"] for fi in component]},
            )

        # A merely contacting entity stays whole in its only volume side.
        if not has_positive or not has_negative:
            target = "positive" if has_positive else "negative"
            for fi in component:
                sides[target].append(dict(faces[fi]))
            continue

        for fi in component:
            face = faces[fi]
            ids = face["indices"]
            local_distances = [distances[i] for i in ids]

            if all(d == 0 for d in local_distances):
                a, b, c = (builder.vertices[i] for i in ids)
                geometric_normal = _cross(_sub(b, a), _sub(c, a))
                # Outward normal +n means the solid is on the negative side.
                target = "negative" if _dot(geometric_normal, normal) > 0 else "positive"
                sides[target].append(dict(face))
            elif min(local_distances) >= 0:
                sides["positive"].append(dict(face))
            elif max(local_distances) <= 0:
                sides["negative"].append(dict(face))
            else:
                for keep_positive in (True, False):
                    polygon = _clip_polygon(
                        face["corners"], distances, builder, normal, offset, keep_positive
                    )
                    if len(polygon) < 3:
                        continue
                    target = "positive" if keep_positive else "negative"
                    for k in range(1, len(polygon) - 1):
                        tri_corners = [polygon[0], polygon[k], polygon[k + 1]]
                        tri_ids = [corner[0] for corner in tri_corners]
                        p0, p1, p2 = (builder.vertices[i] for i in tri_ids)
                        if _norm(_cross(_sub(p1, p0), _sub(p2, p0))) <= eps * eps:
                            continue
                        sides[target].append({
                            "indices": tuple(tri_ids),
                            "corners": tri_corners,
                            "uv": (
                                None if tri_corners[0][1] is None
                                else [corner[1] for corner in tri_corners]
                            ),
                            "material": face["material"],
                            "original_face": face["original_face"],
                        })

    distances = [signed_distance(p) for p in builder.vertices]
    for side_name, side_faces in sides.items():
        _cap_side(side_faces, builder.vertices, distances, normal, offset, side_name, eps)
        _assert_closed(builder.vertices, side_faces, side_name)

    result = {
        name: _compact_mesh(builder.vertices, side_faces)
        for name, side_faces in sides.items()
    }

    original_volume = sum(
        abs(_signed_volume(vertices, [faces[fi] for fi in component]))
        for component in components
    )
    sliced_volume = sum(
        abs(_signed_volume(result[side]["vertices"], result[side]["faces"]))
        for side in result
    )
    if original_volume > eps ** 3:
        relative_error = abs(sliced_volume - original_volume) / original_volume
        if relative_error > 1e-6:
            raise SliceError(
                "signed volume conservation failed",
                {"original_volume": original_volume, "sliced_volume": sliced_volume,
                 "relative_error": relative_error},
            )
    return result


def write_obj(mesh, path):
    lines = []
    vt_index = 0
    vn_index = 0
    for point in mesh["vertices"]:
        lines.append("v " + " ".join(repr(x) for x in point))

    current_material = None
    for face in mesh["faces"]:
        material = face.get("material", "surface")
        if material != current_material:
            lines.append(f"usemtl {material}")
            current_material = material

        normal_index = None
        if face.get("normal") is not None:
            vn_index += 1
            normal_index = vn_index
            lines.append("vn " + " ".join(repr(x) for x in face["normal"]))

        tokens = []
        uv = face.get("uv")
        for corner, vertex_index in enumerate(face["indices"]):
            if uv is not None:
                vt_index += 1
                lines.append("vt " + " ".join(repr(x) for x in uv[corner]))
                if normal_index is not None:
                    tokens.append(f"{vertex_index + 1}/{vt_index}/{normal_index}")
                else:
                    tokens.append(f"{vertex_index + 1}/{vt_index}")
            elif normal_index is not None:
                tokens.append(f"{vertex_index + 1}//{normal_index}")
            else:
                tokens.append(str(vertex_index + 1))
        lines.append("f " + " ".join(tokens))

    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--obj-dir", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text())
        result = slice_mesh(data["mesh"], data["plane"])
        if args.obj_dir:
            args.obj_dir.mkdir(parents=True, exist_ok=True)
            for side, mesh in result.items():
                write_obj(mesh, args.obj_dir / (side + ".obj"))
        args.output.write_text(json.dumps(result, allow_nan=False) + "\n")
    except (SliceError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
