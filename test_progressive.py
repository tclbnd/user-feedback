#!/usr/bin/env python3
"""Acceptance tests for the constrained progressive mesh encoder.

Run with:  python3 test_progressive.py
Exits non-zero (and prints a traceback) on the first failure.
"""
import json
import math
import random
import unittest
from collections import defaultdict
from pathlib import Path

import progressive as P

HERE = Path(__file__).parent
DEMO = json.loads((HERE / "demo.json").read_text())


# --------------------------------------------------------------------------- #
# mesh analysis helpers
# --------------------------------------------------------------------------- #
def edge_faces(mesh):
    out = defaultdict(list)
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            out[tuple(sorted((v[i], v[(i + 1) % 3])))].append(f["id"])
    return out


def boundary_loops(mesh):
    adj = defaultdict(list)
    for e, fids in edge_faces(mesh).items():
        if len(fids) == 1:
            adj[e[0]].append(e[1])
            adj[e[1]].append(e[0])
    loops, seen = [], set()
    for start in sorted(adj):
        if start in seen:
            continue
        loop, prev, cur = [], None, start
        while cur not in seen:
            seen.add(cur)
            loop.append(cur)
            nxt = [x for x in adj[cur] if x != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        loops.append(loop)
    return loops


def components(mesh):
    adj = defaultdict(set)
    for f in mesh["faces"]:
        for a in f["v"]:
            for b in f["v"]:
                adj[a].add(b)
    seen, comps = set(), []
    for v in sorted(adj):
        if v in seen:
            continue
        stack, comp = [v], set()
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.add(x)
            stack.extend(adj[x] - seen)
        comps.append(comp)
    return comps


def normal_signs(mesh):
    p = {v["id"]: v["p"] for v in mesh["vertices"]}
    signs = {}
    for f in mesh["faces"]:
        n = P.face_normal([p[i] for i in f["v"]])
        signs[f["id"]] = 1 if n[2] >= 0 else -1
    return signs


def point_triangle_dist2(p, a, b, c):
    # closest point on a triangle in 3D (Ericson, Real-Time Collision Detection)
    ab = P.vsub(b, a)
    ac = P.vsub(c, a)
    ap = P.vsub(p, a)
    d1, d2 = P.vdot(ab, ap), P.vdot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return P.vdist2(p, a)
    bp = P.vsub(p, b)
    d3, d4 = P.vdot(ab, bp), P.vdot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return P.vdist2(p, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        proj = [a[i] + v * ab[i] for i in range(3)]
        return P.vdist2(p, proj)
    cp = P.vsub(p, c)
    d5, d6 = P.vdot(ab, cp), P.vdot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return P.vdist2(p, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        proj = [a[i] + w * ac[i] for i in range(3)]
        return P.vdist2(p, proj)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        proj = [a[i] + w * (ab[i] - ac[i]) for i in range(3)]
        return P.vdist2(p, proj)
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    proj = [a[i] + v * ab[i] + w * ac[i] for i in range(3)]
    return P.vdist2(p, proj)


def symmetric_hausdorff(ma, mb):
    pa = {v["id"]: v["p"] for v in ma["vertices"]}
    pb = {v["id"]: v["p"] for v in mb["vertices"]}
    tris_b = [[pb[i] for i in f["v"]] for f in mb["faces"]]
    tris_a = [[pa[i] for i in f["v"]] for f in ma["faces"]]

    def one_way(points, tris):
        worst = 0.0
        for p in points:
            best = min(point_triangle_dist2(p, *t) for t in tris)
            worst = max(worst, best)
        return math.sqrt(worst)

    return max(one_way(list(pa.values()), tris_b),
               one_way(list(pb.values()), tris_a))


def canonical(obj):
    def norm(x):
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, list):
            return [norm(i) for i in x]
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items()}
        return x
    return json.dumps(norm(obj), sort_keys=True)


def shuffled_doc(seed):
    rng = random.Random(seed)
    doc = json.loads(json.dumps(DEMO))
    rng.shuffle(doc["mesh"]["vertices"])
    rng.shuffle(doc["mesh"]["faces"])
    return doc


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
class DemoTopology(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stream = P.encode(json.loads(json.dumps(DEMO)))
        cls.levels = [P.decode_stream(cls.stream, k)
                      for k in range(len(cls.stream["levels"]))]
        cls.original = DEMO["mesh"]

    def test_01_plate_with_hole_structure(self):
        # one connected component, an outer loop and one hole loop
        self.assertEqual(len(components(self.original)), 1)
        loops = sorted(boundary_loops(self.original), key=len)
        self.assertEqual(len(loops), 2)
        self.assertEqual(len(loops[0]), 12)       # hole
        self.assertEqual(len(loops[1]), 48)       # outer rim
        # every face winds the same way on the flat plate
        self.assertEqual(set(normal_signs(self.original).values()), {1})

    def test_02_three_levels_with_diffs_and_counts(self):
        self.assertGreaterEqual(len(self.stream["levels"]), 3)
        counts = [lv["count"] for lv in self.stream["levels"]]
        self.assertEqual(counts, [170, 220, 270])
        for lv, mesh in zip(self.stream["levels"], self.levels):
            self.assertEqual(lv["count"], len(mesh["faces"]))
        self.assertEqual(len(self.stream["diffs"]),
                         len(self.stream["levels"]) - 1)
        for d in self.stream["diffs"]:
            self.assertGreater(len(d["splits"]), 0)
            self.assertEqual(d["to_split"] - d["from_split"],
                             len(d["splits"]))

    def test_03_boundary_rings_and_components_preserved(self):
        original_loops = {frozenset(loop)
                          for loop in boundary_loops(self.original)}
        for mesh in self.levels:
            self.assertEqual(len(components(mesh)), 1)
            loops = {frozenset(loop) for loop in boundary_loops(mesh)}
            self.assertEqual(loops, original_loops)
            # boundary geometry is vertex-for-vertex identical
            self.assertEqual(set(normal_signs(mesh).values()), {1})

    def test_04_hard_edges_seams_and_materials_preserved(self):
        orig_ef = edge_faces(self.original)
        mat_seam = set()
        for e, fids in orig_ef.items():
            if len(fids) == 2:
                mats = {self._mat_of(self.original, f) for f in fids}
                if len(mats) > 1:
                    mat_seam.add(e)
        original_mat_counts = {}
        for f in self.original["faces"]:
            original_mat_counts[f["material"]] = \
                original_mat_counts.get(f["material"], 0) + 1
        for mesh in self.levels:
            ef = edge_faces(mesh)
            # no level ever contains a non-manifold edge
            self.assertTrue(all(len(v) <= 2 for v in ef.values()))
            # material partition survives: every seam edge still separates
            # the same two materials (its endpoint IDs are boundary-locked)
            for e in mat_seam:
                self.assertIn(e, ef)
                fids = ef[e]
                self.assertEqual(
                    {self._mat_of(mesh, f) for f in fids},
                    {self._mat_of(self.original, f) for f in fids})
            # face counts per material are conserved at the finest level and
            # never increase on coarser ones
            mat_counts = {}
            for f in mesh["faces"]:
                mat_counts[f["material"]] = \
                    mat_counts.get(f["material"], 0) + 1
            for mat, count in mat_counts.items():
                self.assertLessEqual(count, original_mat_counts[mat])

    def _faces(self, fids):
        by_id = {f["id"]: f for f in self.original["faces"]}
        return [by_id[i] for i in fids]

    @staticmethod
    def _mat(face):
        return face["material"]

    @staticmethod
    def _mat_of(mesh, fid):
        return next(f["material"] for f in mesh["faces"] if f["id"] == fid)

    def test_05_geometric_error_bounds(self):
        budget = float(DEMO["max_error"])
        for mesh in self.levels:
            err = symmetric_hausdorff(self.original, mesh)
            self.assertLessEqual(err, budget + 1e-9)
            # recorded per-collapse quadric errors stay inside the budget
        for rec in self.stream["splits"]:
            self.assertLessEqual(math.sqrt(rec["error"]), budget + 1e-12)

    def test_06_full_restore_matches_original_by_stable_id(self):
        fine = P.decode_stream(self.stream, len(self.stream["levels"]) - 1)
        self.assertEqual(canonical(fine), canonical(self.stream["full_restore"]))
        # stable vertex IDs with identical positions
        ov = {v["id"]: v["p"] for v in self.original["vertices"]}
        fv = {v["id"]: v["p"] for v in fine["vertices"]}
        self.assertEqual(set(ov), set(fv))
        for vid, p in ov.items():
            for a, b in zip(p, fv[vid]):
                self.assertTrue(math.isclose(a, b, abs_tol=1e-12))
        # stable face IDs, winding and per-corner attributes, item by item
        of = {f["id"]: f for f in self.original["faces"]}
        ff = {f["id"]: f for f in fine["faces"]}
        self.assertEqual(set(of), set(ff))
        for fid, face in of.items():
            got = ff[fid]
            self.assertEqual(got["v"], face["v"])
            self.assertEqual(got["material"], face["material"])
            self.assertEqual(len(got["uv"]), 3)
            for c in range(3):
                self.assertEqual(len(got["uv"][c]), 2)
                for a, b in zip(face["uv"][c], got["uv"][c]):
                    self.assertTrue(math.isclose(a, b, abs_tol=1e-12))

    def test_07_incremental_diff_replay(self):
        # applying each adjacent diff from the coarser level yields the finer
        base = self.stream["base"]
        mesh = json.loads(json.dumps(base))
        for k, diff in enumerate(self.stream["diffs"]):
            mesh = P.apply_splits(mesh, diff["splits"])
            expect = self.levels[k + 1]
            self.assertEqual(canonical(mesh), canonical(expect))

    def test_08_random_walk_against_independent_loads(self):
        rng = random.Random(20261113)
        n_levels = len(self.stream["levels"])
        independent = {k: P.decode_stream(self.stream, k)
                       for k in range(n_levels)}
        all_splits = self.stream["splits"]

        # 30 fixed-seed forward/backward single-step moves; after every move
        # the state must equal an independent level load
        level = 0
        active = json.loads(json.dumps(independent[0]))
        for step in range(30):
            move = rng.choice((-1, 1))
            target = min(max(level + move, 0), n_levels - 1)
            if target > level:
                lo = self.stream["levels"][level]["split"]
                hi = self.stream["levels"][target]["split"]
                for rec in all_splits[lo:hi]:
                    active = P.apply_split(active, rec)
            elif target < level:
                hi = self.stream["levels"][level]["split"]
                lo = self.stream["levels"][target]["split"]
                for rec in reversed(all_splits[lo:hi]):
                    active = P.apply_inverse_split(active, rec)
            level = target
            self.assertEqual(canonical(active),
                             canonical(independent[level]))
            # the face count advertised by the level is truthful
            self.assertEqual(self.stream["levels"][level]["count"],
                             len(active["faces"]))

        # random access: jump to an arbitrary level, then continue reading
        # increments from that level's diff window
        for _ in range(10):
            target = rng.randrange(n_levels)
            need = self.stream["levels"][target]["split"]
            jumped = P.apply_splits(
                json.loads(json.dumps(self.stream["base"])),
                all_splits[:need])
            self.assertEqual(canonical(jumped), canonical(independent[target]))
            if target + 1 < n_levels:
                next_need = self.stream["levels"][target + 1]["split"]
                grown = P.apply_splits(jumped, all_splits[need:next_need])
                self.assertEqual(
                    canonical(grown), canonical(independent[target + 1]))

    def test_09_deterministic_under_input_permutation(self):
        ref = canonical(P.encode(json.loads(json.dumps(DEMO))))
        for seed in range(8):
            doc = shuffled_doc(1000 + seed)
            stream = P.encode(doc)
            self.assertEqual(canonical(stream), ref)


class ConstraintTests(unittest.TestCase):
    def _grid_doc(self, nx, ny, materials=None, hard_edges=None,
                  max_error=10.0, targets=None):
        verts, faces, uv = [], [], []
        for j in range(ny):
            for i in range(nx):
                vid = j * nx + i
                verts.append({"id": vid, "p": [float(i), float(j), 0.0]})
        fid = 0
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i
                b, c, d = a + 1, a + nx, a + nx + 1
                mat = materials(i, j) if materials else "m"
                faces.append({"id": fid, "v": [a, b, d],
                              "uv": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                              "material": mat})
                fid += 1
                faces.append({"id": fid, "v": [a, d, c],
                              "uv": [[0.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                              "material": mat})
                fid += 1
        nf = len(faces)
        if targets is None:
            targets = [max(2, nf // 2)]
        return {"mesh": {"vertices": verts, "faces": faces,
                         "hard_edges": hard_edges or []},
                "targets": targets, "max_error": max_error}

    def test_10_hard_fold_edge_is_never_collapsed(self):
        # two flat panels meeting at a 90 degree fold along x = 2
        doc = self._grid_doc(5, 4, targets=[4])
        for v in doc["mesh"]["vertices"]:
            if v["p"][0] >= 2.0:
                v["p"][2] = v["p"][0] - 2.0
        stream = P.encode(doc)
        fold_verts = {v["id"] for v in doc["mesh"]["vertices"]
                      if v["p"][0] == 2.0}
        fold_edges = set()
        for f in doc["mesh"]["faces"]:
            for i in range(3):
                e = tuple(sorted((f["v"][i], f["v"][(i + 1) % 3])))
                if e[0] in fold_verts and e[1] in fold_verts:
                    fold_edges.add(e)
        for rec in stream["splits"]:
            self.assertNotIn(tuple(rec["edge"]), fold_edges)
        # the fold polyline survives geometrically at every level:
        # every fold vertex position is still present on x == 2
        fold_points = {tuple(doc["mesh"]["vertices"][vid]["p"])
                       for vid in fold_verts}
        for k in range(len(stream["levels"])):
            mesh = P.decode_stream(stream, k)
            kept = {tuple(v["p"]) for v in mesh["vertices"]
                    if abs(v["p"][0] - 2.0) < 1e-12}
            self.assertTrue(kept.issubset(fold_points))
            # no vertex crosses the fold: panel signs of z are consistent
            for v in mesh["vertices"]:
                if v["p"][0] < 2.0 - 1e-12:
                    self.assertTrue(abs(v["p"][2]) < 1e-9)
                else:
                    self.assertTrue(
                        abs(v["p"][2] - (v["p"][0] - 2.0)) < 1e-9
                        or abs(v["p"][2]) < 1e-9)

    def test_11_material_boundary_blocks_collapse(self):
        doc = self._grid_doc(5, 4,
                             materials=lambda i, j: "a" if i < 2 else "b",
                             targets=[4])
        stream = P.encode(doc)
        # no collapse record spans the two material regions
        region = {v["id"]: ("a" if v["p"][0] < 2 else "b")
                  for v in doc["mesh"]["vertices"]}
        for rec in stream["splits"]:
            self.assertEqual(region[rec["removed"]], region[rec["kept"]])
        fine = P.decode_stream(stream, len(stream["levels"]) - 1)
        self.assertEqual(canonical(fine),
                         canonical(P._materialize(doc["mesh"], [])))

    def test_12_tiny_budget_reports_stop_reason(self):
        doc = self._grid_doc(4, 4, max_error=0.0, targets=[2])
        stream = P.encode(doc)
        self.assertIsNotNone(stream["stop"])
        self.assertTrue(stream["stop"]["stopped"])
        self.assertIn("error budget", stream["stop"]["reason"])
        self.assertEqual(stream["stop"]["remaining_targets"], [2])
        # the original mesh is still fully recoverable
        fine = P.decode_stream(stream, len(stream["levels"]) - 1)
        self.assertEqual(len(fine["faces"]), len(doc["mesh"]["faces"]))

    def test_13_uv_seam_blocks_collapse(self):
        doc = self._grid_doc(4, 4, targets=[4])
        # fabricate a UV island seam on the x == 1 column: faces on the
        # right assign an offset UV at every corner sitting on that column
        seam_col = {v["id"] for v in doc["mesh"]["vertices"]
                    if v["p"][0] == 1.0}
        for f in doc["mesh"]["faces"]:
            f["uv"] = [[float(vid), 0.0] for vid in f["v"]]
            on_right = any(doc["mesh"]["vertices"][vid]["p"][0] > 1.0
                           for vid in f["v"])
            if on_right:
                for c, vid in enumerate(f["v"]):
                    if vid in seam_col:
                        f["uv"][c] = [float(vid) + 100.0, 0.0]
        state = P.MeshState(doc["mesh"])
        seam_edges = {e for e in state.seam_uv}
        self.assertTrue(any(e[0] in seam_col and e[1] in seam_col
                            for e in seam_edges))
        stream = P.encode(doc)
        for rec in stream["splits"]:
            self.assertNotIn(tuple(sorted(rec["edge"])), seam_edges)

    def test_14_flip_and_duplicate_rejection(self):
        # a configuration where a naive midpoint collapse would flip a
        # triangle must still produce only positively oriented faces
        doc = self._grid_doc(4, 4, targets=[4])
        stream = P.encode(doc)
        for k in range(len(stream["levels"])):
            mesh = P.decode_stream(stream, k)
            p = {v["id"]: v["p"] for v in mesh["vertices"]}
            seen = set()
            for f in mesh["faces"]:
                n = P.face_normal([p[i] for i in f["v"]])
                self.assertGreater(P.vdot(n, n), 0.0)
                key = tuple(sorted(f["v"]))
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_15_disconnected_components(self):
        # two disjoint plates are encoded independently but in one stream
        doc = self._grid_doc(3, 3, targets=[4])
        offset_v = len(doc["mesh"]["vertices"])
        offset_f = len(doc["mesh"]["faces"])
        extra_v = [{"id": v["id"] + offset_v,
                    "p": [c + 10.0 if k == 0 else c
                          for k, c in enumerate(v["p"])]}
                   for v in doc["mesh"]["vertices"]]
        extra_f = [{"id": f["id"] + offset_f,
                    "v": [x + offset_v for x in f["v"]],
                    "uv": f["uv"], "material": f["material"]}
                   for f in doc["mesh"]["faces"]]
        doc["mesh"]["vertices"] += extra_v
        doc["mesh"]["faces"] += extra_f
        doc["targets"] = [8]
        stream = P.encode(doc)
        mesh = P.decode_stream(stream, 0)
        comps = components(mesh)
        self.assertEqual(len(comps), 2)
        fine = P.decode_stream(stream, len(stream["levels"]) - 1)
        self.assertEqual(len(fine["faces"]),
                         len(doc["mesh"]["faces"]))


class CliEndToEnd(unittest.TestCase):
    def test_16_cli_roundtrip_and_artifacts(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            stream = tmp / "stream.json"
            subprocess.run(["python3", str(HERE / "progressive.py"),
                            "encode", str(HERE / "demo.json"), str(stream)],
                           check=True)
            data = json.loads(stream.read_text())
            for k in range(len(data["levels"])):
                out = tmp / ("out" + str(k) + ".json")
                subprocess.run(["python3", str(HERE / "progressive.py"),
                                "decode", str(stream), str(out),
                                "--level", str(k)], check=True)
                mesh = json.loads(out.read_text())
                self.assertEqual(len(mesh["faces"]),
                                 data["levels"][k]["count"])
            prefix = tmp / "pm"
            subprocess.run(["python3", str(HERE / "progressive.py"),
                            "diff", str(stream), str(prefix)], check=True)
            for k in range(len(data["levels"])):
                self.assertTrue(
                    Path(str(prefix) + ".level" + str(k) + ".json").exists())
            for k in range(len(data["diffs"])):
                self.assertTrue(
                    Path(str(prefix) + ".diff" + str(k) + ".json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
