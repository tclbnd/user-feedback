#!/usr/bin/env python3
import math
import unittest

from sss import resample_map, run


def sphere_case(size=129, radius=2.0, sigma=0.35, thick=100.0):
    span = 4.0
    spacing = span / size
    normals = [[[0.0, 0.0, 1.0] for _ in range(size)] for _ in range(size)]
    thickness = [[thick for _ in range(size)] for _ in range(size)]
    for row in range(size):
        y = (row + 0.5) * spacing - span * 0.5
        for col in range(size):
            x = (col + 0.5) * spacing - span * 0.5
            r2 = x * x + y * y
            if r2 < radius * radius:
                z = -math.sqrt(radius * radius - r2)
                length = math.sqrt(r2 + z * z)
                normals[row][col] = [x / length, y / length, z / length]
                thickness[row][col] = radius - z
    req = {
        "normals": normals,
        "thick": thickness,
        "light_dir": [0.0, 0.0, 1.0],
        "sigma": sigma,
        "physical_size": [span, span],
        "seed": 11,
        "tile_size": 8,
    }
    return req, spacing


class SSSValidation(unittest.TestCase):
    def test_sphere_penumbra_matches_one_dimensional_diffusion_profile(self):
        sigma = 0.35
        radius = 2.0
        req, spacing = sphere_case(sigma=sigma, radius=radius, thick=100.0)
        result = run(req)
        center = len(req["normals"]) // 2
        checked = 0
        for col in range(center + 2, center + 9):
            x = (col + 0.5) * spacing - 2.0
            mu = -math.sqrt(1.0 - (x / radius) ** 2)
            reference = math.exp((radius * math.asin(mu)) / sigma)
            value = result["bend"][center][col]
            self.assertLess(abs(value - reference) / reference, 0.11)
            self.assertAlmostEqual(result["curvature"][center][col], 1.0 / radius, delta=0.03)
            checked += 1
        self.assertGreater(checked, 5)

    def test_back_hemisphere_and_thin_slab_are_physical(self):
        size = 16
        normals = [[[0.0, 0.0, -1.0] for _ in range(size)] for _ in range(size)]
        thickness = [[0.00125 * (x + 1) for x in range(size)] for _ in range(size)]
        req = {
            "normals": normals,
            "thick": thickness,
            "light_dir": [0.0, 0.0, 1.0],
            "sigma": 0.4,
            "sigma_thickness": 0.8,
            "albedo": [1.0, 0.25, 0.18],
            "seed": 3,
        }
        result = run(req)
        self.assertTrue(result["dark_zero"])
        for y in range(size):
            for x in range(size):
                for c in range(3):
                    direct_c = result["direct_rgb"][y][x][c]
                    scatter_c = result["scatter_rgb"][y][x][c]
                    expected_slab = math.exp(-thickness[y][x] / 0.8)
                    self.assertLessEqual(direct_c + scatter_c, expected_slab + 1.0e-12)
                    self.assertLessEqual(result["rgba"][y][x][c], 1.0 + 1.0e-7)
                expected = math.exp(-thickness[y][x] / 0.8)
                self.assertAlmostEqual(result["direct"][y][x], expected, delta=1.0e-12)
                self.assertEqual(result["scatter"][y][x], 0.0)
                self.assertEqual(result["thin_mask"][y][x], 1)
        self.assertTrue(result["dark_zero"])

        front = [[[0.0, 0.0, 1.0] for _ in range(size)] for _ in range(size)]
        dark = run(dict(req, normals=front))
        self.assertTrue(dark["dark_zero"])
        self.assertEqual(max(max(row) for row in dark["trans"]), 0.0)
        self.assertEqual(max(max(row) for row in dark["scatter"]), 0.0)

    def test_curved_scatter_conserves_transmitted_slab_energy(self):
        size = 33
        normals = [[[0.0, 0.0, 1.0] for _ in range(size)] for _ in range(size)]
        thickness = [[0.4 for _ in range(size)] for _ in range(size)]
        for y in range(size):
            for x in range(size):
                nx = (x / (size - 1) - 0.5) * 1.2
                nz = -math.sqrt(max(0.0, 1.0 - nx * nx))
                normals[y][x] = [nx, 0.0, nz]
        req = {
            "normals": normals,
            "thick": thickness,
            "light_dir": [0.0, 0.0, 1.0],
            "sigma": 0.5,
            "sigma_thickness": 0.8,
            "albedo": [0.9, 0.5, 0.3],
            "seed": 5,
        }
        result = run(req)
        slab = math.exp(-0.4 / 0.8)
        for y in range(size):
            for x in range(size):
                for c in range(3):
                    total = result["direct_rgb"][y][x][c] + result["scatter_rgb"][y][x][c]
                    self.assertLessEqual(total, slab + 1.0e-12)
                    self.assertGreaterEqual(total, 0.0)

    def test_legacy_32x32_request_keeps_required_channels(self):
        normals = [
            [[0.0, 0.0, -1.0] if x >= 16 else [0.0, 0.0, 1.0] for x in range(32)]
            for _ in range(32)
        ]
        thickness = [[0.05 for _ in range(32)] for _ in range(32)]
        result = run({
            "normals": normals,
            "thick": thickness,
            "light_dir": [0.0, 0.0, 1.0],
            "seed": 1,
        })
        required = {"trans", "curv", "thick", "scatter", "rgba", "summary"}
        self.assertTrue(required.issubset(result))
        self.assertTrue(result["dark_zero"])
        self.assertLessEqual(max(max(row) for row in result["trans"]), 1.0)
        self.assertNotEqual(result["digest"], "")

    def test_uv_stretch_uses_metric_aware_curvature(self):
        size = 129
        radius = 1.0
        span_x, span_y = 4.0, 2.0
        normals = [[[0.0, 0.0, 1.0] for _ in range(size)] for _ in range(size)]
        thickness = [[100.0 for _ in range(size)] for _ in range(size)]
        for row in range(size):
            y = (row + 0.5) * span_y / size - span_y * 0.5
            for col in range(size):
                x = (col + 0.5) * span_x / size - span_x * 0.5
                r2 = x * x + y * y
                if r2 < radius * radius:
                    z = -math.sqrt(radius * radius - r2)
                    normals[row][col] = [x / radius, y / radius, z / radius]
        uv = [
            [[(x + 0.5) / size, (y + 0.5) / size] for x in range(size)]
            for y in range(size)
        ]
        req = {
            "normals": normals,
            "thick": thickness,
            "light_dir": [0.0, 0.0, 1.0],
            "uv": uv,
            "uv_scale": [span_x, span_y],
            "sigma": 0.5,
            "seed": 7,
        }
        result = run(req)
        row = size // 2
        for col in range(size // 2 + 2, size // 2 + 8):
            self.assertAlmostEqual(result["curvature"][row][col], 1.0, delta=0.15)

    def test_tile_order_and_thickness_resolution_are_deterministic(self):
        base, _ = sphere_case(size=64)
        first = run(dict(base, seed=19, tile_size=8))
        second = run(dict(base, seed=91, tile_size=7))
        self.assertEqual(first["rgba"], second["rgba"])
        self.assertEqual(first["digest"], second["digest"])

        low = resample_map(base["thick"], 32, 32)
        low_normals = resample_map(base["normals"], 32, 32)
        low_req = dict(base, thick=low, normals=low_normals, seed=23,
                       roi=[8, 16, 16, 32])
        high_req = dict(base, seed=23, roi=[16, 32, 32, 32])
        low_result = run(low_req)
        high_result = run(high_req)
        self.assertLess(
            abs(low_result["summary"]["scatter_mean"] - high_result["summary"]["scatter_mean"]),
            0.02,
        )
        self.assertLess(
            abs(low_result["summary"]["linear_mean"] - high_result["summary"]["linear_mean"]),
            0.02,
        )
        self.assertLess(
            abs(low_result["summary"]["thickness_mean"] - high_result["summary"]["thickness_mean"]),
            0.10,
        )


if __name__ == "__main__":
    unittest.main()
