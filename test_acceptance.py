"""Acceptance tests for the adaptive octree isosurface extractor.

Run:
    python3 test_acceptance.py

The checks use only the Python standard library.  They cover manifold and
Euler-characteristic invariants, conforming tetrahedral transitions,
geometric-distance bounds, exact-zero grid values, and order independence.
"""

from __future__ import annotations

import collections
import itertools
import math

from surface import Octree, TetraMesh, extract, extract_surface


BASE_DOMAIN = [-1.7, 1.7]
THRESHOLDS = (0.20, 0.08, 0.03)


def make_config(kind: str, tolerance: float, seed: int) -> dict:
    config = {
        "field": kind,
        "domain": BASE_DOMAIN,
        "tolerance": tolerance,
        "max_depth": 7,
        "seed": seed,
    }
    if kind == "sphere":
        config["radius"] = 0.75
    elif kind == "shell":
        config["inner"] = 0.45
        config["outer"] = 0.75
    else:
        config["center"] = [0.04, -0.03, 0.02]
        config["major"] = 0.83
        config["minor"] = 0.31
    return config


def build(config: dict):
    tree = Octree(config)
    tree.build()
    tetmesh = TetraMesh(tree)
    tetmesh.build(seed=int(config["seed"]))
    mesh = extract_surface(config, tree, tetmesh)
    return tree, tetmesh, mesh


def surface_topology(mesh: dict) -> dict:
    edge_occurrences = collections.Counter()
    oriented_edge_sum = collections.Counter()
    duplicate_faces = set()
    face_keys = set()
    for face in mesh["faces"]:
        key = tuple(face)
        if key in face_keys:
            duplicate_faces.add(key)
        face_keys.add(key)
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            undirected = tuple(sorted((a, b)))
            edge_occurrences[undirected] += 1
            oriented_edge_sum[undirected] += 1 if a < b else -1

    adjacency = collections.defaultdict(list)
    for face in mesh["faces"]:
        for a, b in zip(face, face[1:] + face[:1]):
            adjacency[a].append(b)
            adjacency[b].append(a)
    seen = set()
    components = 0
    for start in range(len(mesh["vertices"])):
        if start in seen:
            continue
        components += 1
        stack = [start]
        seen.add(start)
        while stack:
            vertex = stack.pop()
            for other in adjacency[vertex]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)

    v_count = len(mesh["vertices"])
    f_count = len(mesh["faces"])
    e_count = len(edge_occurrences)
    return {
        "V": v_count,
        "E": e_count,
        "F": f_count,
        "chi": v_count - e_count + f_count,
        "components": components,
        "non_manifold_edges": [
            edge for edge, count in edge_occurrences.items() if count != 2
        ],
        "unpaired_oriented_edges": [
            edge for edge, value in oriented_edge_sum.items() if value != 0
        ],
        "duplicate_faces": duplicate_faces,
    }


def assert_balanced(tree: Octree) -> None:
    for depth, i, j, k in tree.leaves:
        for di, dj, dk in itertools.product((-1, 0, 1), repeat=3):
            if di == dj == dk == 0:
                continue
            other = tree.occupant(depth, i + di, j + dj, k + dk)
            if other is not None:
                assert abs(other[0] - depth) <= 1


def assert_conforming_tets(tetmesh: TetraMesh) -> None:
    triangle_to_tets = collections.defaultdict(list)
    for tet_index, tet in enumerate(tetmesh.tetrahedra):
        for combination in itertools.combinations(range(4), 3):
            triangle = tuple(sorted(tet[index] for index in combination))
            triangle_to_tets[triangle].append(tet_index)
    duplicates = [
        triangle for triangle, tets in triangle_to_tets.items() if len(tets) > 2
    ]
    assert not duplicates


def analytic_distance(config: dict, point) -> float:
    cx, cy, cz = config.get("center", [0.0, 0.0, 0.0])
    x, y, z = point[0] - cx, point[1] - cy, point[2] - cz
    radius = math.hypot(x, y, z)
    if config["field"] == "sphere":
        return abs(radius - config["radius"])
    if config["field"] == "shell":
        return min(abs(radius - config["outer"]), abs(radius - config["inner"]))
    q = math.hypot(x, y)
    return abs(math.hypot(q - config["major"], z) - config["minor"])


def leaf_diagonal_at(tree: Octree, point) -> float:
    for depth in range(tree.max_depth + 1):
        count = 1 << depth
        h = tree.size / count
        indices = []
        for value in point:
            index = math.floor((value - tree.lo) / h)
            indices.append(min(count - 1, max(0, index)))
        leaf = tree.occupant(depth, *indices)
        if leaf is not None and leaf[0] == depth:
            return h * math.sqrt(3.0)
    raise AssertionError("point is not contained in an octree leaf")


def analytic_samples(config: dict):
    center = config.get("center", [0.0, 0.0, 0.0])
    if config["field"] == "sphere":
        radii = [config["radius"]]
    elif config["field"] == "shell":
        radii = [config["inner"], config["outer"]]
    else:
        for i in range(24):
            u = 2.0 * math.pi * i / 24.0
            for j in range(12):
                v = 2.0 * math.pi * j / 12.0
                radial = config["major"] + config["minor"] * math.cos(v)
                yield (
                    radial * math.cos(u) + center[0],
                    radial * math.sin(u) + center[1],
                    config["minor"] * math.sin(v) + center[2],
                )
        return

    for radius in radii:
        for i in range(16):
            u = 2.0 * math.pi * i / 16.0
            for j in range(8):
                # Fibonacci-like deterministic sphere parameter values.
                z = -1.0 + 2.0 * (j + 0.5) / 8.0
                planar = math.sqrt(max(0.0, 1.0 - z * z))
                angle = u + j * 0.37
                yield (
                    center[0] + radius * planar * math.cos(angle),
                    center[1] + radius * planar * math.sin(angle),
                    center[2] + radius * z,
                )


def point_distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def assert_geometry_bounds(config: dict, tree: Octree, mesh: dict) -> tuple:
    vertices = [tuple(v) for v in mesh["vertices"]]
    mesh_to_surface = []
    mesh_bounds = []

    # Mesh-to-analytic-surface: every output vertex lies inside the diagonal
    # bound of the leaf that contains it.
    for point in vertices:
        bound = leaf_diagonal_at(tree, point)
        error = analytic_distance(config, point)
        mesh_to_surface.append(error)
        mesh_bounds.append(bound)
        assert error <= bound

    # Analytic-surface-to-output-mesh over a deterministic directional sample.
    surface_to_mesh = []
    surface_bounds = []
    for sample in analytic_samples(config):
        bound = leaf_diagonal_at(tree, sample)
        nearest = min(point_distance(sample, point) for point in vertices)
        surface_to_mesh.append(nearest)
        surface_bounds.append(bound)
        assert nearest <= bound + 1.0e-12
    return (max(mesh_to_surface), max(mesh_bounds),
            max(surface_to_mesh), max(surface_bounds))


def test_topology_transitions_and_geometry() -> None:
    expected = {
        "sphere": (1, 2),
        "shell": (2, 4),
        "torus": (1, 0),
    }
    summaries = {}
    for kind in ("sphere", "shell", "torus"):
        previous_faces = None
        summaries[kind] = []
        for tolerance in THRESHOLDS:
            config = make_config(kind, tolerance, seed=1)
            tree, tetmesh, mesh = build(config)
            topo = surface_topology(mesh)

            assert not topo["non_manifold_edges"]
            assert not topo["unpaired_oriented_edges"]
            assert not topo["duplicate_faces"]
            assert (topo["components"], topo["chi"]) == expected[kind]
            assert_balanced(tree)
            assert_conforming_tets(tetmesh)
            geometry = assert_geometry_bounds(config, tree, mesh)

            if previous_faces is not None:
                assert len(mesh["faces"]) > previous_faces
            previous_faces = len(mesh["faces"])
            summaries[kind].append(
                (tolerance, len(tree.leaves), len(mesh["vertices"]),
                 len(mesh["faces"]), dict(mesh["stats"]["leaf_depths"]),
                 geometry)
            )
    print("threshold summaries:")
    for kind, rows in summaries.items():
        for (tolerance, leaf_count, vertex_count, face_count,
             depths, geometry) in rows:
            mesh_error, mesh_bound, sample_error, sample_bound = geometry
            print(
                f"  {kind:6s} tol={tolerance:.2f} leaves={leaf_count:5d} "
                f"V={vertex_count:6d} F={face_count:6d} depths={depths}\n"
                f"          geometric error mesh->surface={mesh_error:.6f} "
                f"<= diagonal {mesh_bound:.6f}; "
                f"surface->mesh={sample_error:.6f} <= diagonal {sample_bound:.6f}"
            )


def test_five_leaf_permutations_are_identical() -> None:
    for kind in ("sphere", "shell", "torus"):
        canonical = None
        for tolerance in THRESHOLDS:
            for seed in (10, 11, 12, 13, 14):
                config = make_config(kind, tolerance, seed)
                mesh = extract(config)
                summary = (mesh["vertices"], mesh["faces"], mesh["stats"])
                if canonical is None:
                    canonical = summary
                else:
                    assert summary == canonical
            canonical = None
    print("five randomized leaf orderings per setting produce identical output")


def test_exact_zero_grid_values() -> None:
    # Domain endpoints make r=0.5,0.25,... coincide with grid coordinates.
    for radius in (0.5, 0.25, 0.125):
        config = {
            "field": "sphere",
            "domain": [-1.0, 1.0],
            "radius": radius,
            "tolerance": 0.15,
            "max_depth": 6,
            "seed": 1,
        }
        outputs = []
        for seed in range(5):
            config["seed"] = seed
            outputs.append(extract(config))
        assert all(
            (m["vertices"], m["faces"], m["stats"]) ==
            (outputs[0]["vertices"], outputs[0]["faces"], outputs[0]["stats"])
            for m in outputs
        )
        topo = surface_topology(outputs[0])
        assert (topo["components"], topo["chi"]) == (1, 2)
        assert not topo["non_manifold_edges"]
        assert not topo["unpaired_oriented_edges"]
    print("exact-zero grid intersections share vertices and remain watertight")


if __name__ == "__main__":
    test_topology_transitions_and_geometry()
    test_five_leaf_permutations_are_identical()
    test_exact_zero_grid_values()
    print("all acceptance tests passed")
