#!/usr/bin/env python3
"""Acceptance tests for tangent.py.

Run with:  python3 -m unittest -v tests_tangent
"""

import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest

import tangent

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- vector maths
def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vlen(a):
    return math.sqrt(vdot(a, a))


def angle_deg(a, b):
    la, lb = vlen(a), vlen(b)
    if la == 0.0 or lb == 0.0:
        return 180.0
    c = max(-1.0, min(1.0, vdot(a, b) / (la * lb)))
    return math.degrees(math.acos(c))


def corner_frame(res, ci):
    """(vertex, tangent, w) for output corner index ``ci``."""
    t = res["tangents"][ci]
    return res["corner_vertex"][ci], (t[0], t[1], t[2]), t[3]


def tri_normal(mesh, ci):
    """Unnormalised face normal of the triangle owning corner ``ci``."""
    tri = mesh["tris"][ci // 3]
    p = [tuple(mesh["pos"][v]) for v in tri]
    return vcross(vsub(p[1], p[0]), vsub(p[2], p[0]))


def split_by_vertex(res):
    table = {}
    for ci in range(len(res["tangents"])):
        table.setdefault(res["corner_vertex"][ci], []).append(
            tuple(res["tangents"][ci]))
    return table


# --------------------------------------------------------------- mesh builders
class Cylinder:
    """Cylinder around +Y with u wrapped around and v along the axis.

    ``closed`` controls whether the last quad wraps onto the first column.  An
    open cylinder has complete vertex fans everywhere, so the analytic tangent
    is reproduced to machine precision; a closed one necessarily carries a UV
    seam, where the tangent is reconstructed from the one-sided fan only.

    ``mirrored`` is a predicate on the quad index; a mirrored quad gets its UV
    chart mirrored in u, exactly like a mirrored UV island.
    """

    def __init__(self, segments=128, radius=1.0, height=2.0,
                 mirrored=lambda seg: False, per_corner=True, closed=True):
        self.segments = segments
        self.radius = radius
        self.height = height
        self.mirrored = mirrored
        self.per_corner = per_corner
        self.closed = closed
        self.mesh, self.expected, self.mirrored_corner = self._build()

    def _vid(self, ring, i):
        cols = self.segments + 1          # pos layout: always closed columns
        if self.per_corner and self.closed:
            i %= self.segments
        return ring * cols + i

    def _uv(self, seg, ring, quad):
        """UV of the corner for column ``seg`` of quad ``quad``.

        The mirror sign is a property of the *chart* (the quad), never of a
        single corner, so all corners of a quad stay consistent.
        """
        u = seg / self.segments
        if self.mirrored(quad):
            u = -u
        return [u, float(ring)]

    def _build(self):
        segments = self.segments
        cols = segments + 1
        pos = []
        uv = []
        for ring in range(2):
            for i in range(cols):
                th = 2.0 * math.pi * i / segments
                pos.append([self.radius * math.cos(th),
                            self.height * ring,
                            self.radius * math.sin(th)])
                if not self.per_corner:
                    uv.append([i / segments, float(ring)])

        quads = range(segments) if self.closed else range(segments - 1)

        tris = []
        corner_uv = []
        expected = []
        mirrored_corner = []
        for seg in quads:
            a = (self._vid(0, seg), seg, 0)
            b = (self._vid(0, seg + 1), seg + 1, 0)
            c = (self._vid(1, seg), seg, 1)
            d = (self._vid(1, seg + 1), seg + 1, 1)
            for face in ((a, b, d), (a, d, c)):
                tris.append([f[0] for f in face])
                for _vid, fseg, ring in face:
                    corner_uv.append(self._uv(fseg, ring, seg))
                    # analytic dp/du of the cylinder chart; a mirrored chart
                    # maps u -> -u and therefore flips dp/du.
                    th = 2.0 * math.pi * fseg / segments
                    tx, tz = -math.sin(th), math.cos(th)
                    if self.mirrored(seg):
                        tx, tz = -tx, -tz
                    expected.append((tx, 0.0, tz))
                    mirrored_corner.append(self.mirrored(seg))

        mesh = {"pos": pos, "uv": corner_uv if self.per_corner else uv,
                "tris": tris}
        return mesh, expected, mirrored_corner

    def max_tangent_error(self, res):
        worst = 0.0
        n = len(res["tangents"])
        for ci in range(n):
            _, t, _w = corner_frame(res, ci)
            worst = max(worst, angle_deg(t, self.expected[ci]))
        return worst


def add_zero_area_patch(mesh):
    """Append a triangle whose two UV edges are zero -> zero UV area."""
    base = len(mesh["pos"])
    mesh["pos"] += [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    per_corner = len(mesh["uv"]) == 3 * len(mesh["tris"])
    mesh["tris"].append([base, base + 1, base + 2])
    if per_corner:
        mesh["uv"] += [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]
    else:
        mesh["uv"] += [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]
    return base


def mirror_plane():
    """Butterfly across x = 0: left chart normal, right chart mirrored in u.

    Vertices 0 and 2 are shared by both charts, so a naive per-vertex
    accumulation cancels their tangents exactly.
    """
    pos = [[0.0, -1.0, 0.0],     # 0 a  (shared)
           [-1.0, 0.0, 0.0],     # 1 b
           [0.0, 1.0, 0.0],      # 2 c  (shared)
           [1.0, 0.0, 0.0]]      # 3 d
    tris = [[0, 2, 1], [0, 3, 2]]
    corner_uv = [
        [0.25, 0.00], [0.25, 0.50], [0.00, 0.25],   # a, c, b   normal chart
        [0.75, 0.00], [0.50, 0.25], [0.75, 0.50],   # a, d, c   mirrored chart
    ]
    return {"pos": pos, "uv": corner_uv, "tris": tris}


# ---------------------------------------------------------------------- tests
class TestMirroredIslands(unittest.TestCase):
    def test_bitangent_axes_flip_and_stay_unit(self):
        mesh = mirror_plane()
        res = tangent.run(mesh)
        n = tri_normal(mesh, 0)
        n = tuple(c / vlen(n) for c in n)

        for ci in range(3):
            _, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0, places=9)
            self.assertAlmostEqual(vdot(t, n), 0.0, places=9)
            self.assertAlmostEqual(w, 1.0)
        for ci in range(3, 6):
            _, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0, places=9)
            self.assertAlmostEqual(vdot(t, n), 0.0, places=9)
            self.assertAlmostEqual(w, -1.0)

        t_left = corner_frame(res, 0)[1]
        t_right = corner_frame(res, 3)[1]
        self.assertAlmostEqual(t_left[0], 1.0, places=9)
        self.assertAlmostEqual(t_right[0], -1.0, places=9)

    def test_bitangent_cross_products_are_opposite_unit_vectors(self):
        mesh = mirror_plane()
        res = tangent.run(mesh)
        n = tri_normal(mesh, 0)
        n = tuple(c / vlen(n) for c in n)
        frames = []
        for ci in (0, 3):
            _v, t, w = corner_frame(res, ci)
            frames.append(vcross(n, t))
        bl, br = frames
        self.assertAlmostEqual(vlen(bl), 1.0, places=9)
        self.assertAlmostEqual(vlen(br), 1.0, places=9)
        self.assertAlmostEqual(vdot(bl, br), -1.0, places=9)

    def test_shared_positions_are_split_not_cancelled(self):
        mesh = mirror_plane()
        res = tangent.run(mesh)
        table = split_by_vertex(res)
        # vertex 0 and 2 appear in both charts with opposite handedness ->
        # 2 distinct split keys each; vertex 1 and 3 in one chart only -> 1
        self.assertEqual(len(table[0]), 2)
        self.assertEqual(len(table[2]), 2)
        self.assertEqual(len(table[1]), 1)
        self.assertEqual(len(table[3]), 1)
        self.assertEqual(res["split_vertices"], 6)

        # each of the two split keys of vertex 0 has a unit tangent along +x/-x
        ts = sorted(round(t[0], 9) for t in table[0])
        self.assertEqual(ts, [-1.0, 1.0])
        ws = sorted(t[3] for t in table[0])
        self.assertEqual(ws, [-1.0, 1.0])


class TestAnalyticCylinder(unittest.TestCase):
    def _check(self, cyl, skip=()):
        res = tangent.run(cyl.mesh)
        self.assertEqual(len(res["tangents"]), 3 * len(cyl.mesh["tris"]))
        for ci in range(len(res["tangents"])):
            _, t, _w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0, places=9)
        worst = 0.0
        for ci in range(len(res["tangents"])):
            if ci in skip:
                continue
            _, t, _w = corner_frame(res, ci)
            worst = max(worst, angle_deg(t, cyl.expected[ci]))
        self.assertLessEqual(worst, 0.5,
                             "worst per-corner tangent error %.8f deg" % worst)
        return res

    def test_closed_cylinder_is_exact(self):
        # no UV seam and no mesh boundary -> the tangent is reproduced exactly
        res = self._check(Cylinder(segments=128, per_corner=True))
        self.assertEqual(res["degenerate"], 0)

    def test_per_corner_uv_seam(self):
        # A real UV seam: u jumps from 1.0 back to 0.0 on the closing quad.
        # The seam must not be treated as a tangent discontinuity, because both
        # sides describe the same geometric surface.
        cyl = Cylinder(segments=96, per_corner=True)
        res = self._check(cyl)
        # seam column splits into two keys with different u but the same tangent
        seam_pairs = {}
        for ci in range(len(res["tangents"])):
            vid = res["corner_vertex"][ci]
            u = cyl.mesh["uv"][ci][0]
            seam_pairs.setdefault(vid, {}).setdefault(round(u, 6), None)
        multi = [v for v, uvs in seam_pairs.items() if len(uvs) > 1]
        self.assertTrue(multi, "expected at least one UV-seam vertex")

    def test_mirrored_islands_flip_handedness(self):
        cyl = Cylinder(segments=128, per_corner=True,
                       mirrored=lambda seg: seg < 38)     # ~30%
        res = tangent.run(cyl.mesh)
        flip_boundary = 38
        for ci in range(len(res["tangents"])):
            _v, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0, places=9)
            want = -1.0 if cyl.mirrored_corner[ci] else 1.0
            self.assertAlmostEqual(w, want)

        # Island interiors must reproduce the analytic tangent.  The only
        # corners that cannot are those of a deliberately split vertex (a
        # position used by both handedness groups): each group then sees a
        # one-sided vertex fan only, which biases the estimate by Delta_theta/2
        # and vanishes as the tessellation is refined.
        split_vids = set()
        handedness = {}
        for ci in range(len(res["tangents"])):
            vid = res["corner_vertex"][ci]
            handedness.setdefault(vid, set()).add(res["tangents"][ci][3])
        for vid, signs in handedness.items():
            if len(signs) > 1:
                split_vids.add(vid)

        worst_split_free = 0.0
        for ci in range(len(res["tangents"])):
            if res["corner_vertex"][ci] in split_vids:
                continue
            _v, t, _w = corner_frame(res, ci)
            worst_split_free = max(worst_split_free,
                                   angle_deg(t, cyl.expected[ci]))
        self.assertLessEqual(worst_split_free, 0.5,
                             "worst non-split tangent error %.6f deg"
                             % worst_split_free)

        # ...and at the split vertices the tangent is still exact: the normal is
        # accumulated per position (geometry), so the two handedness groups use
        # the same, correct normal and Gram-Schmidt reconstructs the analytic
        # tangent on both sides.
        bias_bound = 1e-6
        for ci in range(len(res["tangents"])):
            if res["corner_vertex"][ci] not in split_vids:
                continue
            _v, t, _w = corner_frame(res, ci)
            self.assertLessEqual(angle_deg(t, cyl.expected[ci]), bias_bound)

    def test_mirrored_island_error_independent_of_tessellation(self):
        def worst(segs):
            cyl = Cylinder(segments=segs, per_corner=True,
                           mirrored=lambda s, n=segs: s < int(round(0.30 * n)))
            res = tangent.run(cyl.mesh)
            return max(angle_deg(corner_frame(res, ci)[1], cyl.expected[ci])
                       for ci in range(len(res["tangents"])))

        for segs in (64, 128, 256):
            self.assertLessEqual(worst(segs), 1e-6)


class TestOrderIndependence(unittest.TestCase):
    def _model(self):
        cyl = Cylinder(segments=60, per_corner=True,
                       mirrored=lambda seg: seg < 18)      # 30% mirrored
        add_zero_area_patch(cyl.mesh)
        return cyl.mesh

    def test_shuffled_faces_same_digest(self):
        mesh = self._model()
        base = tangent.run(mesh)
        n_tris = len(mesh["tris"])
        uvs = [[mesh["uv"][3 * t + k] for k in range(3)] for t in range(n_tris)]
        rng = random.Random(1234)

        for _ in range(8):
            order = list(range(n_tris))
            rng.shuffle(order)
            sh_tris = []
            sh_uv = []
            for old in order:
                tri = list(mesh["tris"][old])
                tuv = list(uvs[old])
                r = rng.randrange(3)                 # cyclic rotation too
                sh_tris.append(tri[r:] + tri[:r])
                sh_uv.extend(tuv[r:] + tuv[:r])
            shuffled = {"pos": mesh["pos"], "uv": sh_uv, "tris": sh_tris}
            got = tangent.run(shuffled)
            self.assertEqual(got["digest"], base["digest"])
            self.assertEqual(got["split_tangents"], base["split_tangents"])
            self.assertEqual(got["degenerate"], base["degenerate"])
            self.assertEqual(got["fallback_keys"], base["fallback_keys"])
            self.assertEqual(got["split_vertices"], base["split_vertices"])

    def test_per_corner_summary_identical_after_shuffle(self):
        mesh = self._model()
        base = split_by_vertex(tangent.run(mesh))
        n_tris = len(mesh["tris"])
        uvs = [[mesh["uv"][3 * t + k] for k in range(3)] for t in range(n_tris)]
        rng = random.Random(99)
        order = list(range(n_tris))
        rng.shuffle(order)
        sh_tris = [mesh["tris"][i] for i in order]
        sh_uv = []
        for i in order:
            sh_uv.extend(uvs[i])
        got = split_by_vertex(tangent.run({"pos": mesh["pos"], "uv": sh_uv,
                                           "tris": sh_tris}))
        self.assertEqual(sorted(base), sorted(got))
        for vid in base:
            self.assertEqual(sorted(base[vid]), sorted(got[vid]))


class TestDegenerateFallback(unittest.TestCase):
    def test_zero_uv_area_uses_documented_basis(self):
        mesh = {"pos": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                "uv": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                "tris": [[0, 1, 2]]}
        res = tangent.run(mesh)
        self.assertEqual(res["degenerate"], 1)
        self.assertEqual(res["degenerate_corners"], 3)
        # one fallback split key per degenerate vertex -> 3
        self.assertEqual(res["fallback_keys"], 3)
        self.assertEqual(res["fallback_corners"], 3)
        n = (0.0, 0.0, 1.0)
        for ci in range(3):
            _, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0, places=12)
            self.assertAlmostEqual(vdot(t, n), 0.0, places=12)
            self.assertAlmostEqual(t[0], 1.0, places=12)    # ref = +X
            self.assertAlmostEqual(w, 1.0)

    def test_x_aligned_normal_uses_y_reference(self):
        mesh = {"pos": [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "uv": [[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]],
                "tris": [[0, 1, 2]]}
        res = tangent.run(mesh)
        self.assertEqual(res["degenerate"], 1)
        for ci in range(3):
            _, t, _w = corner_frame(res, ci)
            self.assertAlmostEqual(t[1], 1.0, places=12)
            self.assertAlmostEqual(t[0], 0.0, places=12)

    def test_collinear_uv_is_degenerate(self):
        mesh = {"pos": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                "uv": [[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]],
                "tris": [[0, 1, 2]]}
        res = tangent.run(mesh)
        self.assertEqual(res["degenerate"], 1)
        for ci in range(3):
            _, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0)
            self.assertAlmostEqual(w, 1.0)

    def test_mixed_mesh_counts(self):
        cyl = Cylinder(segments=24, per_corner=True)
        add_zero_area_patch(cyl.mesh)
        res = tangent.run(cyl.mesh)
        self.assertEqual(res["degenerate"], 1)
        self.assertEqual(res["degenerate_corners"], 3)
        self.assertEqual(len(res["tangents"]), 3 * len(cyl.mesh["tris"]))
        self.assertEqual(res["fallback_corners"], 3)
        # the patch has three distinct degenerate vertices -> three fallback keys
        self.assertEqual(res["fallback_keys"], 3)

    def test_zero_area_normal_is_zero(self):
        # fully collapsed position triangle: normal is zero as well
        mesh = {"pos": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
                "uv": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                "tris": [[0, 1, 2]]}
        res = tangent.run(mesh)
        for ci in range(3):
            _, t, w = corner_frame(res, ci)
            self.assertAlmostEqual(vlen(t), 1.0)
            self.assertEqual(w, 1.0)


class TestCli(unittest.TestCase):
    def test_cli_roundtrip(self):
        mesh = mirror_plane()
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mesh.json")
            dst = os.path.join(d, "out.json")
            with open(src, "w") as fh:
                json.dump(mesh, fh)
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "tangent.py"), src, dst],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(dst) as fh:
                out = json.load(fh)
        self.assertEqual(len(out["tangents"]), 6)
        self.assertEqual(out["digest"], tangent.run(mesh)["digest"])
        self.assertEqual(out["bitangent_sign"],
                         [t[3] for t in out["tangents"]])

    def test_cli_on_indexed_uv_mesh(self):
        cyl = Cylinder(segments=24, per_corner=False)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mesh.json")
            dst = os.path.join(d, "out.json")
            with open(src, "w") as fh:
                json.dump(cyl.mesh, fh)
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "tangent.py"), src, dst],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(dst) as fh:
                out = json.load(fh)
        self.assertEqual(out["uv_mode"], "vertex")
        self.assertEqual(len(out["tangents"]), 3 * len(cyl.mesh["tris"]))
        self.assertEqual(out["degenerate"], 0)

    def test_usage_error(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "tangent.py")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)


if __name__ == "__main__":
    unittest.main()
