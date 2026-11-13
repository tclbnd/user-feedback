"""Acceptance tests for cross-face cubemap sampling and prefiltering.

Run: python3 test_cube.py
"""
import math
import unittest

import cube

FACES = cube.FACES
AXES = (
    (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(d):
    return cube._norm(*d)


# ---------------------------------------------------------------------------
# Independent spherical reference: explicit per-face orthonormal bases,
# implemented without reusing cube.direction / cube.locate arithmetic.
# ---------------------------------------------------------------------------
BASES = {
    # face: (normal, u-axis, v-axis) with dir = n + u*ua + v*va
    "+x": ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
    "-x": ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "+y": ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
    "-y": ((0, -1, 0), (1, 0, 0), (0, 0, -1)),
    "+z": ((0, 0, 1), (1, 0, 0), (0, -1, 0)),
    "-z": ((0, 0, -1), (-1, 0, 0), (0, -1, 0)),
}


def ref_locate(d):
    x, y, z = d
    ax = abs(x)
    ay = abs(y)
    az = abs(z)
    if ax >= ay and ax >= az:
        face = "+x" if x >= 0 else "-x"
    elif ay >= az:
        face = "+y" if y >= 0 else "-y"
    else:
        face = "+z" if z >= 0 else "-z"
    n, ua, va = BASES[face]
    denom = dot(d, n)
    return face, dot(d, ua) / denom, dot(d, va) / denom


def ref_dir(face, u, v):
    n, ua, va = BASES[face]
    return norm((
        n[0] + u * ua[0] + v * va[0],
        n[1] + u * ua[1] + v * va[1],
        n[2] + u * ua[2] + v * va[2],
    ))


def ref_nearest(faces, face, u, v):
    n = len(faces[face])
    if -1.0 <= u < 1.0 and -1.0 <= v < 1.0:
        i = cube._nearest_index(u, n)
        j = cube._nearest_index(v, n)
        return faces[face][j][i]
    # Cross-face remap through the physical 3-D direction (edge + corner).
    f2, u2, v2 = ref_locate(ref_dir(face, u, v))
    m = len(faces[f2])
    return faces[f2][cube._nearest_index(v2, m)][cube._nearest_index(u2, m)]


def ref_bilinear(faces, d):
    face, u, v = ref_locate(d)
    n = len(faces[face])
    i0 = int(math.floor((u + 1.0) * 0.5 * n - 0.5))
    j0 = int(math.floor((v + 1.0) * 0.5 * n - 0.5))
    u0 = cube._center(i0, n)
    v0 = cube._center(j0, n)
    fu = min(1.0, max(0.0, (u - u0) * 0.5 * n))
    fv = min(1.0, max(0.0, (v - v0) * 0.5 * n))
    vals = [
        ref_nearest(faces, face, cube._center(i, n), cube._center(j, n))
        for (i, j) in ((i0, j0), (i0 + 1, j0), (i0, j0 + 1), (i0 + 1, j0 + 1))
    ]
    top = vals[0] * (1 - fu) + vals[1] * fu
    bot = vals[2] * (1 - fu) + vals[3] * fu
    return top * (1 - fv) + bot * fv


# ---------------------------------------------------------------------------
# Analytic continuous environment and its rotation.
# ---------------------------------------------------------------------------
LOBE_AXES = (norm((1.0, 0.4, -0.2)), norm((-0.5, 0.8, 0.6)), norm((0.2, -1.0, 0.3)))


def env(d):
    s = 0.3
    s += 0.8 * max(0.0, dot(d, LOBE_AXES[0])) ** 4
    s += 0.5 * max(0.0, dot(d, LOBE_AXES[1])) ** 8
    s += 0.35 * max(0.0, dot(d, LOBE_AXES[2])) ** 12
    return s


def rotate(axis, angle):
    """Rodrigues rotation matrix R(v) = v cos a + (k x v) sin a + k(k.v)(1-cos)."""
    c = math.cos(angle)
    s = math.sin(angle)
    x, y, z = axis

    def apply(v):
        vx, vy, vz = v
        cx = y * vz - z * vy
        cy = z * vx - x * vz
        cz = x * vy - y * vx
        kd = x * vx + y * vy + z * vz
        return (
            vx * c + cx * s + x * kd * (1 - c),
            vy * c + cy * s + y * kd * (1 - c),
            vz * c + cz * s + z * kd * (1 - c),
        )

    return apply


ROT = rotate(norm((0.3, 0.7, -0.5)), 0.9)


def rotated_env(d):
    return env(ROT(d))


def rasterize(n, func=rotated_env):
    return {
        face: [
            [func(norm(cube.direction(face, cube._center(i, n), cube._center(j, n))))
             for i in range(n)]
            for j in range(n)
        ]
        for face in FACES
    }


def constant_faces(n, value):
    row = [value] * n
    return {face: [list(row) for _ in range(n)] for face in FACES}


def edge_and_corner_directions():
    """All 12 edge axes, 8 corner diagonals, plus both-side perturbations."""
    dirs = []
    signs = (-1.0, 1.0)
    # corners
    for sx in signs:
        for sy in signs:
            for sz in signs:
                dirs.append(norm((sx, sy, sz)))
    # edges: one component zero, two at +/-1
    for zero in range(3):
        for sa in signs:
            for sb in signs:
                base = [0.0, 0.0, 0.0]
                comps = [sa, sb]
                k = 0
                for c in range(3):
                    if c != zero:
                        base[c] = comps[k]
                        k += 1
                dirs.append(norm(tuple(base)))
    # perturbed both-side samples around every edge and corner
    eps = 1e-3
    for base in list(dirs):
        for axis in range(3):
            delta = [0.0, 0.0, 0.0]
            delta[axis] = eps
            dirs.append(norm(tuple(base[c] + delta[c] for c in range(3))))
            dirs.append(norm(tuple(base[c] - delta[c] for c in range(3))))
    return dirs


class CrossFaceSamplingTests(unittest.TestCase):

    def test_direction_locate_inverse(self):
        for face in FACES:
            for u in (-0.9, -0.31, 0.0, 0.27, 0.88):
                for v in (-0.77, -0.1, 0.4, 0.95):
                    f2, u2, v2 = cube.locate(cube.direction(face, u, v))
                    self.assertEqual(f2, face)
                    self.assertAlmostEqual(u2, u, places=12)
                    self.assertAlmostEqual(v2, v, places=12)

    def test_solid_angle_sums_to_4pi(self):
        for n in (1, 2, 4, 8, 16):
            total = sum(
                cube.texel_solid_angle(f, i, j, n)
                for f in FACES for j in range(n) for i in range(n)
            )
            self.assertLess(abs(total - 4 * math.pi), 1e-12)

    def test_constant_environment_edges_corners(self):
        faces = constant_faces(16, 0.4)
        worst = 0.0
        for d in edge_and_corner_directions():
            value = cube.sample(faces, d)
            worst = max(worst, abs(value - 0.4))
        self.assertLessEqual(worst, 1e-5, worst)

    def test_constant_prefilter_all_roughness(self):
        # Per-pixel solid angle weighting must keep a constant env constant.
        faces = constant_faces(12, 0.63)
        data = {
            "faces": faces,
            "directions": [[1, 0, 0], [1, 1, 1], [0, -1, 0.2]],
            "roughness": [0.0, 0.25, 0.5, 0.75, 1.0, 1.3],
            "sizes": [12, 12, 8, 6, 4, 2],
            "seed": 7,
        }
        result = cube.run(data)
        worst = 0.0
        for level in result["levels"]:
            for face in FACES:
                for row in level["faces"][face]:
                    worst = max(worst, max(abs(v - 0.63) for v in row))
            worst = max(worst, max(abs(v - 0.63) for v in level["samples"]))
        self.assertLessEqual(worst, 1e-10, worst)

    def test_matches_independent_spherical_reference(self):
        # Structurally independent bilinear implementation (explicit bases).
        faces = rasterize(16)
        rng = deterministic_dirs(400)
        interior_worst = 0.0
        for d in rng:
            self.assertAlmostEqual(cube.sample(faces, d), ref_bilinear(faces, d),
                                   delta=1e-12)
            f, u, v = ref_locate(d)
            if abs(u) <= 0.8 and abs(v) <= 0.8:
                interior_worst = max(
                    interior_worst, abs(cube.sample(faces, d) - rotated_env(d))
                )
        # Across every edge and corner, both sides.
        edge_worst = 0.0
        for d in edge_and_corner_directions():
            got = cube.sample(faces, d)
            self.assertAlmostEqual(got, ref_bilinear(faces, d), delta=1e-12)
            edge_worst = max(edge_worst, abs(got - rotated_env(d)))
        # Cross-face error must not exceed the same-resolution tolerance
        # established by independent-direction sampling in face interiors.
        self.assertLessEqual(edge_worst, interior_worst + 1e-12,
                             (edge_worst, interior_worst))

    def test_scale_invariance(self):
        faces = rasterize(8)
        for d in deterministic_dirs(50):
            base = cube.sample(faces, d)
            for s in (0.07, 3.14, 1234.5):
                self.assertAlmostEqual(
                    cube.sample(faces, tuple(c * s for c in d)), base, delta=1e-14
                )
            # A negative scale denotes the antipodal physical direction.
            self.assertAlmostEqual(
                cube.sample(faces, tuple(-2.5 * c for c in d)),
                cube.sample(faces, tuple(-c for c in d)),
                delta=1e-14,
            )

    def test_no_seam_black_line(self):
        # A smoothly varying env must not dip at the seam: values on the two
        # sides of an edge must approach each other as epsilon -> 0.
        faces = rasterize(24)
        base = norm((1.0, 1.0, 0.0))
        prev_gap = None
        for eps in (1e-1, 1e-2, 1e-3, 1e-4):
            a = cube.sample(faces, norm((1.0, 1.0, eps)))
            b = cube.sample(faces, norm((1.0, 1.0, -eps)))
            gap = abs(a - b)
            if prev_gap is not None:
                self.assertLessEqual(gap, prev_gap + 1e-12)
            prev_gap = gap
        self.assertLess(prev_gap, 1e-4)


def deterministic_dirs(count):
    """Fixed pseudo-random unit directions (no RNG dependency)."""
    dirs = []
    for k in range(count):
        x = math.sin(k * 12.9898) * 43758.5453
        y = math.sin(k * 78.233 + 1.7) * 12543.123
        z = math.sin(k * 37.719 + 4.2) * 9876.543
        dirs.append(norm((x - math.floor(x) - 0.5,
                          y - math.floor(y) - 0.5,
                          z - math.floor(z) - 0.5)))
    return dirs


class PrefilterTests(unittest.TestCase):

    def sphere_reference(self, d, roughness):
        """High-sample deterministic spherical integral of the given kernel."""
        k = cube.kernel_exponent(roughness)
        n_theta = 90
        n_phi = 180
        dtheta = math.pi / n_theta
        dphi = 2 * math.pi / n_phi
        num = 0.0
        den = 0.0
        for it in range(n_theta):
            theta = (it + 0.5) * dtheta
            st = math.sin(theta)
            ct = math.cos(theta)
            for ip in range(n_phi):
                phi = (ip + 0.5) * dphi
                n = (st * math.cos(phi), st * math.sin(phi), ct)
                cosine = dot(d, n)
                if cosine <= 0.0:
                    continue
                weight = cosine ** (k + 1.0) * st * dtheta * dphi
                num += rotated_env(n) * weight
                den += weight
        return num / den

    def test_prefilter_against_spherical_integral(self):
        source_n = 24
        faces = rasterize(source_n)
        roughnesses = [0.25, 0.5, 0.75, 1.0, 1.3]
        sizes = [24, 16, 12, 8, 4]
        # Pre-specified directions: face centers, edges, corners, fixed set.
        directions = list(AXES)
        directions += [
            norm((1, 1, 0)), norm((1, -1, 0.3)), norm((0.2, 1, -1)),
            norm((-1, 1, 1)), norm((1, -1, -1)),
        ]
        directions += deterministic_dirs(6)
        data = {
            "faces": faces,
            "directions": directions,
            "roughness": roughnesses,
            "sizes": sizes,
            "seed": 7,
        }
        result = cube.run(data)
        overall = []
        for level, r in zip(result["levels"], roughnesses):
            self.assertEqual(level["roughness"], float(r))
            errs = []
            ref_mean = 0.0
            for d, got in zip(directions, level["samples"]):
                ref = self.sphere_reference(d, r)
                errs.append(abs(got - ref))
                ref_mean += ref
            ref_mean /= len(directions)
            normalized_mean = sum(errs) / len(errs) / ref_mean
            overall.append(normalized_mean)
            self.assertLessEqual(
                normalized_mean, 0.03,
                "roughness %s normalized mean error %s" % (r, normalized_mean),
            )
        # Explicit roughness -> level mapping is positional and ordered.
        self.assertEqual(
            [lv["roughness"] for lv in result["levels"]],
            [float(r) for r in roughnesses],
        )
        self.assertEqual([lv["size"] for lv in result["levels"]], sizes)

    def test_reproducible_seed_and_schema(self):
        faces = rasterize(8)
        payload = {
            "faces": faces,
            "directions": [[1, 1, 0], [1, 1, 1], [0, -1, 0.5]],
            "roughness": [0, 0.75],
            "sizes": [8, 4],
            "seed": 7,
        }
        import json
        a = json.dumps(cube.run(payload), sort_keys=True)
        b = json.dumps(cube.run(payload), sort_keys=True)
        payload2 = dict(payload)
        del payload2["seed"]
        c = json.dumps(cube.run(payload2), sort_keys=True)
        self.assertEqual(a, b)
        # Deterministic integration: the declared seed does not alter output.
        self.assertEqual(a, c)
        result = json.loads(a)
        self.assertEqual(set(result), {"samples", "levels"})
        for level in result["levels"]:
            self.assertEqual(set(level), {"roughness", "size", "samples", "faces"})
            self.assertEqual(len(level["samples"]), len(payload["directions"]))
            self.assertEqual(set(level["faces"]), set(FACES))
            for face in FACES:
                self.assertEqual(len(level["faces"][face]), level["size"])
                self.assertEqual(len(level["faces"][face][0]), level["size"])
        # r = 0 samples are the cross-face bilinear values.
        self.assertEqual(result["levels"][0]["samples"], result["samples"])

    def test_bright_spot_energy_constrained(self):
        spot_axis = norm((0.6, 0.3, -0.7))

        def spot_env(d):
            return 0.2 + 9.8 * max(0.0, dot(d, spot_axis)) ** 64

        faces = rasterize(24, spot_env)
        roughnesses = [0.15, 0.3, 0.6, 1.0]
        data = {
            "faces": faces,
            "directions": [list(spot_axis)],
            "roughness": roughnesses,
            "sizes": [24, 16, 12, 8],
        }
        values = [level["samples"][0] for level in cube.run(data)["levels"]]
        # Prefiltered radiance always stays inside the env's value range.
        for value in values:
            self.assertGreaterEqual(value, 0.2 - 1e-12)
            self.assertLessEqual(value, 10.0 + 1e-9)
        # Energy spreads with growing roughness: the hot centre must cool,
        # never brighten beyond the source peak.
        for a, b in zip(values, values[1:]):
            self.assertLessEqual(b, a + 1e-9)
        # Hemisphere average is a hard upper bound on the blurred value.
        table = cube.build_source_table(faces)
        weighted = 0.0
        weight = 0.0
        for unit, radiance, omega in table:
            cosine = max(0.0, dot(spot_axis, unit))
            if cosine > 0.0:
                w = cosine * omega
                weighted += radiance * w
                weight += w
        hemisphere_mean = weighted / weight
        self.assertLessEqual(values[-1], hemisphere_mean + 1e-9)

    def test_kernel_exponent_mapping(self):
        self.assertEqual(cube.kernel_exponent(1.0), 0.0)
        self.assertEqual(cube.kernel_exponent(2.0), 0.0)
        self.assertAlmostEqual(cube.kernel_exponent(0.75),
                               2.0 / 0.75 ** 2 - 2.0)
        self.assertAlmostEqual(cube.kernel_exponent(0.5), 6.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
