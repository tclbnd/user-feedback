"""Acceptance tests for the dual-quaternion skinning fix.

Run with ``python3 -m unittest discover -s tests`` from the repository root.
"""

import json
import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import skin  # noqa: E402


# --------------------------------------------------------------------------
# Independent reference dual-quaternion skinning (textbook formulation).
# --------------------------------------------------------------------------

def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def q_conj(a):
    return (a[0], -a[1], -a[2], -a[3])


def reference_skin(data):
    """Straightforward DLB reference: every bone -> (q_r, q_d), blend, apply."""
    bones = {}
    for bone in data["bones"]:
        q = [float(v) for v in bone["rotation"]]
        length = math.sqrt(sum(v * v for v in q))
        if length == 0.0:
            q = [1.0, 0.0, 0.0, 0.0]
        else:
            q = [v / length for v in q]
        t = [float(v) for v in bone["translation"]]
        dual = q_mul((0.0, t[0], t[1], t[2]), tuple(q))
        dual = tuple(0.5 * v for v in dual)
        bones[bone["id"]] = (tuple(q), dual)

    out = []
    for vertex in data["vertices"]:
        entries = {}
        for item in vertex["weights"]:
            w = float(item["weight"])
            if w > 0.0:
                entries[item["bone"]] = entries.get(item["bone"], []) + [w]
        entries = {k: sum(v) for k, v in entries.items()}
        items = sorted(((w, k) for k, w in entries.items() if w > 0),
                       key=lambda pair: (-pair[0], pair[1]))
        if not items:
            out.append(list(vertex["position"]))
            continue
        total = sum(w for w, _ in items)
        items = [(w / total, k) for w, k in items]
        anchor = bones[items[0][1]][0]
        qs, ds, ws = [], [], []
        for w, bone_id in items:
            q, d = bones[bone_id]
            dot = sum(a * b for a, b in zip(q, anchor))
            if dot < 0.0:
                q = tuple(-v for v in q)
                d = tuple(-v for v in d)
            elif dot == 0.0:
                first = next((v for v in q if v != 0.0), 1.0)
                if first < 0.0:
                    q = tuple(-v for v in q)
                    d = tuple(-v for v in d)
            qs.append(q)
            ds.append(d)
            ws.append(w)
        q = tuple(math.fsum(w * qi[k] for w, qi in zip(ws, qs)) for k in range(4))
        d = tuple(math.fsum(w * di[k] for w, di in zip(ws, ds)) for k in range(4))
        n = math.sqrt(math.fsum(v * v for v in q))
        q = tuple(v / n for v in q)
        d = tuple(v / n for v in d)
        t = q_mul(d, q_conj(q))
        translation = (2.0 * t[1], 2.0 * t[2], 2.0 * t[3])
        out.append(skin.transform(vertex["position"],
                                  {"rotation": q, "translation": translation}))
    return out


# --------------------------------------------------------------------------
# Synthetic rigs.
# --------------------------------------------------------------------------

def twist_quaternion(angle):
    return [math.cos(angle / 2.0), math.sin(angle / 2.0), 0.0, 0.0]


def wrist_rig(angle, radius=0.1, samples=64, x=0.6):
    """A ring of vertices around the limb axis (x), split 50/50 over the joint."""
    vertices = []
    for index in range(samples):
        phi = 2.0 * math.pi * index / samples
        vertices.append({
            "position": [x, radius * math.cos(phi), radius * math.sin(phi)],
            "weights": [{"bone": "forearm", "weight": 0.5},
                        {"bone": "hand", "weight": 0.5}],
        })
    return {
        "bones": [
            {"id": "forearm", "rotation": [1.0, 0.0, 0.0, 0.0],
             "translation": [0.0, 0.0, 0.0]},
            {"id": "hand", "rotation": twist_quaternion(angle),
             "translation": [0.0, 0.0, 0.0]},
        ],
        "vertices": vertices,
    }


def linear_skin(data):
    """The original linear blend baseline, kept for comparison."""
    bones = {bone["id"]: bone for bone in data["bones"]}
    out = []
    for vertex in data["vertices"]:
        total = sum(w["weight"] for w in vertex["weights"])
        if total == 0.0:
            out.append(list(vertex["position"]))
            continue
        blended = [0.0, 0.0, 0.0]
        for item in vertex["weights"]:
            p = skin.transform(vertex["position"], bones[item["bone"]])
            for axis in range(3):
                blended[axis] += p[axis] * item["weight"] / total
        out.append(blended)
    return out


def circumference(points):
    return math.fsum(
        math.dist(points[index], points[(index + 1) % len(points)])
        for index in range(len(points)))


def ring_retention(data):
    bind = [vertex["position"] for vertex in data["vertices"]]
    dq = [vertex["position"] for vertex in skin.skin(data)["vertices"]]
    lb = linear_skin(data)
    return circumference(dq) / circumference(bind), circumference(lb) / circumference(bind)


def rest_rig(seed=7):
    """All bone transforms are the identity: this *is* the bind pose."""
    rng = random.Random(seed)
    bones = [
        {"id": "root", "rotation": [1.0, 0.0, 0.0, 0.0], "translation": [0.0, 0.0, 0.0]},
        {"id": "forearm", "rotation": [1.0, 0.0, 0.0, 0.0], "translation": [0.0, 0.0, 0.0]},
        {"id": "hand", "rotation": [1.0, -0.0, -0.0, -0.0], "translation": [0.0, 0.0, 0.0]},
    ]
    vertices = []
    for _ in range(40):
        position = [rng.uniform(0.0, 1.0), rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2)]
        weights = [
            {"bone": "root", "weight": rng.random()},
            {"bone": "forearm", "weight": rng.random()},
            {"bone": "hand", "weight": rng.random()},
        ]
        vertices.append({"position": position, "weights": weights})
    vertices.append({"position": [0.5, 0.1, 0.0], "weights": []})
    vertices.append({"position": [0.5, -0.1, 0.0],
                     "weights": [{"bone": "hand", "weight": 0.0}]})
    vertices.append({"position": [0.7, 0.0, 0.1],
                     "weights": [{"bone": "forearm", "weight": 2.5}]})
    return {"bones": bones, "vertices": vertices}


def posed_rig(seed=11, angle=math.radians(250.0)):
    rng = random.Random(seed)
    rig = rest_rig(seed)
    rig["bones"][2]["rotation"] = twist_quaternion(angle)
    rig["bones"][2]["translation"] = [0.8, 0.05, -0.02]
    rig["bones"][1]["rotation"] = [math.cos(0.3), 0.0, 0.0, math.sin(0.3)]
    for vertex in rig["vertices"]:
        for index in range(len(vertex["weights"])):
            vertex["weights"][index]["weight"] *= rng.uniform(0.1, 10.0)
    return rig


class TwistTest(unittest.TestCase):

    def test_wrist_circumference_survives_twist(self):
        for degrees in (180.0, 270.0):
            data = wrist_rig(math.radians(degrees))
            dq_retention, lb_retention = ring_retention(data)
            self.assertGreater(dq_retention, 0.999,
                               "dual quaternion lost circumference at %g deg" % degrees)
            self.assertGreater(dq_retention, lb_retention + 0.2,
                               "no improvement over linear blend at %g deg" % degrees)
        # 180 deg is the worst case for linear blend: the ring degenerates.
        _, lb180 = ring_retention(wrist_rig(math.pi))
        self.assertLess(lb180, 0.01)

    def test_matches_reference_dual_quaternion(self):
        for degrees in (180.0, 270.0):
            data = wrist_rig(math.radians(degrees), radius=0.13)
            result = skin.skin(data)["vertices"]
            reference = reference_skin(data)
            for index, (vertex, expected) in enumerate(zip(result, reference)):
                error = max(abs(a - b) for a, b in zip(vertex["position"], expected))
                self.assertLessEqual(error, 1e-4,
                                     "vertex %d off by %g" % (index, error))

    def test_reference_agreement_on_random_pose(self):
        data = posed_rig()
        result = skin.skin(data)["vertices"]
        reference = reference_skin(data)
        for vertex, expected in zip(result, reference):
            error = max(abs(a - b) for a, b in zip(vertex["position"], expected))
            self.assertLessEqual(error, 1e-4)

    def test_bind_pose_is_exact(self):
        data = rest_rig()
        result = skin.skin(data)
        for vertex, source in zip(result["vertices"], data["vertices"]):
            self.assertEqual(vertex["position"], list(source["position"]))
        self.assertEqual(result["stats"]["max_weight_sum_error"], 0.0)

    def test_zero_weight_and_single_bone_vertices(self):
        data = rest_rig()
        # Pose the rig; unweighted vertices must not move at all.
        data["bones"][1]["rotation"] = twist_quaternion(1.0)
        data["bones"][1]["translation"] = [0.4, 0.3, 0.0]
        result = skin.skin(data)
        untouched = result["vertices"][40]
        self.assertEqual(untouched["position"], [0.5, 0.1, 0.0])
        self.assertEqual(untouched["method"], "bind")
        zeroed = result["vertices"][41]
        self.assertEqual(zeroed["position"], [0.5, -0.1, 0.0])
        self.assertEqual(zeroed["method"], "bind")
        single = result["vertices"][42]
        expected = skin.transform([0.7, 0.0, 0.1], data["bones"][1])
        self.assertEqual(single["position"], expected)
        self.assertEqual(single["method"], "dual_quaternion")
        for actual, reference in zip(single["position"], linear_skin(data)[42]):
            self.assertAlmostEqual(actual, reference, places=12)
        self.assertEqual(result["stats"]["max_weight_sum_error"], 0.0)

    def test_single_bone_vertices_match_linear_blend_exactly(self):
        rng = random.Random(19)
        bones = [
            {"id": "a", "rotation": twist_quaternion(0.0), "translation": [0.0, 0.0, 0.0]},
            {"id": "b", "rotation": twist_quaternion(math.radians(200.0)),
             "translation": [0.25, -0.1, 0.4]},
            {"id": "c", "rotation": [0.3, 0.5, -0.2, 0.7], "translation": [0.0, 1.0, 0.0]},
        ]
        for bone in bones:
            norm = math.sqrt(sum(v * v for v in bone["rotation"]))
            bone["rotation"] = [v / norm for v in bone["rotation"]]
        data = {"bones": bones, "vertices": []}
        for index in range(60):
            data["vertices"].append({
                "position": [rng.uniform(-1, 1) for _ in range(3)],
                "weights": [{"bone": "abc"[index % 3],
                             "weight": rng.uniform(0.0, 5.0)}],
            })
        result = skin.skin(data)
        baseline = linear_skin(data)
        for vertex, expected in zip(result["vertices"], baseline):
            self.assertEqual(vertex["method"], "dual_quaternion")
            # A single rigid transform: matches linear blend to rounding.
            for actual, reference in zip(vertex["position"], expected):
                self.assertAlmostEqual(actual, reference, places=12)
        self.assertEqual(result["stats"]["degenerate_single_bone"], 60)

    def test_degenerate_dual_quaternion_blend_falls_back(self):
        self.assertIsNone(skin._blend_dual_quaternions(
            [(1.0, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0))]))
        # The fallback itself is the weighted average of the rigid transforms.
        bones = {
            "a": {"bone": {"rotation": [1.0, 0.0, 0.0, 0.0],
                           "translation": [1.0, 0.0, 0.0]}},
            "b": {"bone": {"rotation": [0.0, 1.0, 0.0, 0.0],
                           "translation": [0.0, 0.0, 0.0]}},
        }
        blended = skin._linear_blend([0.0, 1.0, 0.0],
                                     [(0.25, (0.0, "a")), (0.75, (0.0, "b"))],
                                     bones)
        expected_a = skin.transform([0.0, 1.0, 0.0], bones["a"]["bone"])
        expected_b = skin.transform([0.0, 1.0, 0.0], bones["b"]["bone"])
        for axis in range(3):
            self.assertAlmostEqual(
                blended[axis],
                0.25 * expected_a[axis] + 0.75 * expected_b[axis], places=15)

    def test_methods_and_position_stats(self):
        data = posed_rig()
        result = skin.skin(data)
        methods = {vertex["method"] for vertex in result["vertices"]}
        self.assertEqual(methods, {"dual_quaternion", "bind"})
        stats = result["stats"]
        positions = [vertex["position"] for vertex in result["vertices"]]
        for axis in range(3):
            self.assertEqual(stats["position_min"][axis],
                             min(p[axis] for p in positions))
            self.assertEqual(stats["position_max"][axis],
                             max(p[axis] for p in positions))
            centroid = math.fsum(p[axis] for p in positions) / len(positions)
            self.assertAlmostEqual(stats["position_centroid"][axis], centroid,
                                   places=12)
        self.assertEqual(stats["bind_vertices"], 2)
        self.assertEqual(stats["degenerate_single_bone"], 1)
        self.assertEqual(sum(stats["method_counts"].values()), len(positions))


class DeterminismTest(unittest.TestCase):

    def _shuffled(self, data, seed):
        rng = random.Random(seed)
        shuffled = json.loads(json.dumps(data))
        rng.shuffle(shuffled["bones"])
        for vertex in shuffled["vertices"]:
            rng.shuffle(vertex["weights"])
        return shuffled

    def test_bone_order_does_not_matter(self):
        data = posed_rig()
        baseline = skin.skin(data)
        for seed in range(8):
            shuffled = self._shuffled(data, seed)
            result = skin.skin(shuffled)
            self.assertEqual(result["digest"], baseline["digest"])
            self.assertEqual(result["vertices"], baseline["vertices"])
            self.assertEqual(result["stats"]["max_weight_sum_error"], 0.0)
            self.assertEqual(result["stats"], baseline["stats"])

    def test_digest_tracks_positions(self):
        first = skin.skin(posed_rig(angle=math.radians(180.0)))
        second = skin.skin(posed_rig(angle=math.radians(270.0)))
        self.assertNotEqual(first["digest"], second["digest"])

    def test_duplicate_weight_entries_merge(self):
        data = {
            "bones": [
                {"id": "a", "rotation": [1.0, 0.0, 0.0, 0.0], "translation": [0.0, 0.0, 0.0]},
                {"id": "b", "rotation": [0.0, 1.0, 0.0, 0.0], "translation": [1.0, 0.0, 0.0]},
            ],
            "vertices": [
                {"position": [0.0, 1.0, 0.0],
                 "weights": [{"bone": "a", "weight": 0.25},
                             {"bone": "b", "weight": 0.25},
                             {"bone": "a", "weight": 0.5}]},
                {"position": [0.0, 1.0, 0.0],
                 "weights": [{"bone": "b", "weight": 0.25},
                             {"bone": "a", "weight": 0.75}]},
            ],
        }
        result = skin.skin(data)
        self.assertEqual(result["vertices"][0]["position"],
                         result["vertices"][1]["position"])
        self.assertEqual(result["stats"]["max_weight_sum_error"], 0.0)

    def test_weights_with_extreme_ratios_stay_normalized(self):
        data = {
            "bones": [
                {"id": "a", "rotation": [1.0, 0.0, 0.0, 0.0], "translation": [0.0, 0.0, 0.0]},
                {"id": "b", "rotation": twist_quaternion(2.0), "translation": [0.0, 0.0, 0.0]},
                {"id": "c", "rotation": twist_quaternion(-1.0), "translation": [0.3, 0.0, 0.0]},
            ],
            "vertices": [],
        }
        rng = random.Random(3)
        for index in range(200):
            data["vertices"].append({
                "position": [0.1 * index / 200.0, 0.2, -0.1],
                "weights": [
                    {"bone": "a", "weight": rng.random() * 10.0 ** rng.randint(-9, 9)},
                    {"bone": "b", "weight": rng.random() * 10.0 ** rng.randint(-9, 9)},
                    {"bone": "c", "weight": rng.random() * 10.0 ** rng.randint(-9, 9)},
                ],
            })
        result = skin.skin(data)
        self.assertEqual(result["stats"]["max_weight_sum_error"], 0.0)
        for vertex in result["vertices"]:
            self.assertTrue(all(math.isfinite(v) for v in vertex["position"]))
        for vertex, expected in zip(result["vertices"], reference_skin(data)):
            error = max(abs(a - b) for a, b in zip(vertex["position"], expected))
            self.assertLessEqual(error, 1e-4)


class CliTest(unittest.TestCase):

    def test_cli_round_trip(self):
        import tempfile

        data = posed_rig()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            target = Path(directory) / "output.json"
            source.write_text(json.dumps(data))
            skin_main = ROOT / "skin.py"
            import subprocess

            subprocess.run([sys.executable, str(skin_main), str(source), str(target)],
                           check=True)
            written = json.loads(target.read_text())
        self.assertEqual(written, json.loads(json.dumps(skin.skin(data))))


if __name__ == "__main__":
    unittest.main()
