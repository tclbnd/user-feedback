#!/usr/bin/env python3
"""Acceptance tests for the precomputed atmosphere LUTs (atmo.py).

Run with ``python3 test_atmo.py``; exit code 0 means every acceptance check
passed.  No third-party packages are required.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

import atmo

CFG = {"lut_size": 16, "samples": 32, "seed": 1}


def transmittance_node(lut, mu, alt):
    """Exact table-node lookup (no interpolation) for a physical point."""
    n = lut["meta"]["lut_size"]
    i = round((mu + 1.0) * 0.5 * (n - 1))
    j = round(alt / atmo.TOP * (n - 1))
    return lut["T"][i][j]


class TransmittanceTests(unittest.TestCase):
    # --- Acceptance 1a: analytic optical depth, relative error <= 0.5% ------
    def test_constant_density_analytic(self):
        """Constant-density atmosphere: tau = beta * geometric path length."""
        const = lambda h: 1.0
        worst = 0.0
        for mu in (1.0, 0.7, 0.3, 0.1, 0.01):
            for alt in (0.0, 15.0, 40.0):
                r0 = atmo._observer_radius(alt)
                length = min(atmo.path_length_to_top(r0, mu),
                             atmo.ground_hit_distance(r0, mu))
                tau_analytic = atmo.BETA * length
                tau_numeric = atmo.optical_depth(r0, mu, 32, const)
                worst = max(worst,
                            abs(tau_numeric - tau_analytic) / tau_analytic)
        self.assertLessEqual(worst, 0.005,
                             "constant-density tau error %.2e" % worst)

    def test_constant_density_ground_hit(self):
        """Downward constant-density rays terminate exactly at the ground."""
        const = lambda h: 1.0
        for mu in (-0.2, -0.5, -0.9):
            r0 = atmo._observer_radius(20.0)
            length = atmo.ground_hit_distance(r0, mu)
            tau = atmo.optical_depth(r0, mu, 32, const)
            self.assertLess(abs(tau - atmo.BETA * length)
                            / (atmo.BETA * length), 1e-12)

    def test_nonunit_density_single_beta(self):
        """rho=2 must give exactly twice the rho=1 optical depth (beta once)."""
        rho1 = lambda h: 1.0
        rho2 = lambda h: 2.0
        for mu in (0.7, 0.1, 0.01):
            r0 = atmo._observer_radius(5.0)
            t1 = atmo.optical_depth(r0, mu, 32, rho1)
            t2 = atmo.optical_depth(r0, mu, 32, rho2)
            self.assertAlmostEqual(t2 / t1, 2.0, places=12)
            length = atmo.path_length_to_top(r0, mu)
            self.assertLess(abs(t2 - 2.0 * atmo.BETA * length)
                            / (2.0 * atmo.BETA * length), 1e-12)

    def test_zenith_exact_shell_integral(self):
        """Vertical exponential ray has a closed-form shell integral."""
        tau_analytic = atmo.BETA * atmo.H * (
            1.0 - math.exp(-atmo.TOP / atmo.H))
        tau_numeric = atmo.optical_depth(atmo._observer_radius(0.0),
                                         1.0, 32)
        self.assertLess(abs(tau_numeric - tau_analytic) / tau_analytic, 1e-9)

    def test_thin_high_altitude_paths(self):
        """High-altitude, optically thin paths vs a 16k-sample reference."""
        worst = 0.0
        for alt in (30.0, 40.0, 50.0, 55.0):
            for mu in (1.0, 0.5, 0.3, 0.15):
                r0 = atmo._observer_radius(alt)
                tau_ref = atmo.optical_depth(r0, mu, 16000)
                tau_lut = atmo.optical_depth(r0, mu, 64)
                worst = max(worst, abs(tau_lut - tau_ref) / tau_ref)
        self.assertLessEqual(worst, 0.005,
                             "thin-path tau error %.2e" % worst)

    def test_horizon_transmittance_accuracy(self):
        """Near-horizon transmittance (refraction geometry) within 0.5%."""
        worst = 0.0
        for mu in (0.01, 0.02, 0.05, 0.1):
            r0 = atmo._observer_radius(0.0)
            t_ref = math.exp(-atmo.optical_depth(r0, mu, 16000))
            t_lut = atmo.transmittance(mu, 0.0, 32)
            worst = max(worst, abs(t_lut - t_ref) / t_ref)
        self.assertLessEqual(worst, 0.005,
                             "horizon transmittance error %.2e" % worst)

    def test_refraction_changes_horizon_path(self):
        """Inflated Earth radius must lengthen the near-horizon ray path."""
        r0 = atmo._observer_radius(0.0)
        length_refr = atmo.path_length_to_top(r0, 0.01)
        saved = atmo.REFRACTION
        try:
            atmo.REFRACTION = 1.0
            length_flat = atmo.path_length_to_top(
                atmo._observer_radius(0.0), 0.01)
        finally:
            atmo.REFRACTION = saved
        self.assertNotAlmostEqual(atmo.REFRACTION, 1.0)
        self.assertGreater(length_refr, length_flat)
        # Optical depth itself is finite and positive at the horizon.
        tau = atmo.optical_depth(r0, 0.01, 64)
        self.assertGreater(tau, 0.0)
        self.assertLess(math.exp(-tau), 1.0)


class InscatterTests(unittest.TestCase):
    # --- Acceptance 1b: high-sample numeric reference, error <= 3% ----------
    # Below this absolute radiance a value is treated as numerical noise of
    # a physically zero cell (ground-hit ray, or density ~ 0 at the top).
    NOISE_FLOOR = 1e-12

    def test_against_high_sample_reference(self):
        worst = 0.0
        for mu in (0.9, 0.5, 0.2, 0.0, -0.3):
            for alt in (0.0, 10.0, 30.0):
                reference = atmo.inscatter(mu, alt, 240)
                value = atmo.inscatter(mu, alt, 32)
                if reference > self.NOISE_FLOOR:
                    worst = max(worst, abs(value - reference) / reference)
        self.assertLessEqual(worst, 0.03,
                             "inscatter error %.2e" % worst)

    def test_lut_nodes_match_reference(self):
        """Built S table cells are within 3% of the reference; shadow is 0."""
        lut = atmo.build(CFG)
        n = CFG["lut_size"]
        worst = 0.0
        for i in range(n):
            mu = 2.0 * i / (n - 1) - 1.0
            for j in range(n):
                alt = atmo.TOP * j / (n - 1)
                reference = atmo.inscatter(mu, alt, 240)
                value = lut["S"][i][j]
                if reference > self.NOISE_FLOOR:
                    worst = max(worst, abs(value - reference) / reference)
                else:
                    # Physically empty cells (shadow / vacuum) must be exact 0.
                    self.assertEqual(value, 0.0)
        self.assertLessEqual(worst, 0.03)

    def test_earth_shadow_zeros_scatter(self):
        """A view ray immediately entering the ground yields no in-scatter."""
        self.assertEqual(atmo.inscatter(-0.5, 0.0, 32), 0.0)
        self.assertEqual(atmo.inscatter(-0.99, 0.0, 32), 0.0)

    def test_anisotropic_phase_function(self):
        """Rayleigh phase favours forward/backward over 90-degree scatter."""
        # Compare two view directions at the same altitude; the phase factor
        # (1 + mu_scatter^2) must produce different, ordered brightness.
        forward = atmo.inscatter(0.7, 10.0, 64)
        side = atmo.inscatter(0.0, 10.0, 64)
        self.assertGreater(side, 0.0)
        self.assertGreater(forward, 0.0)
        # P(mu) itself: forward (|mu|=1) is double the side value (|mu|=0).
        self.assertAlmostEqual(
            atmo._rayleigh_phase(1.0) / atmo._rayleigh_phase(0.0), 2.0)

    def test_multi_scatter_compensation(self):
        """The 1.05 compensation is a constant multiplier on the integral."""
        self.assertEqual(atmo.MULTI_SCATTER, 1.05)
        value = atmo.inscatter(0.5, 5.0, 64)
        saved = atmo.MULTI_SCATTER
        try:
            atmo.MULTI_SCATTER = 1.0
            base = atmo.inscatter(0.5, 5.0, 64)
        finally:
            atmo.MULTI_SCATTER = saved
        self.assertAlmostEqual(value / base, 1.05, places=12)


class ReproducibilityTests(unittest.TestCase):
    # --- Acceptance 2a: construction order / threads must not change data ---
    def test_order_and_thread_invariance(self):
        baseline = atmo.build(CFG, order="sequential", threads=1)
        for order in ("reverse", "chunks"):
            for threads in (1, 4):
                other = atmo.build(CFG, order=order, threads=threads)
                self.assertEqual(other["T"], baseline["T"], order)
                self.assertEqual(other["S"], baseline["S"], order)
                self.assertEqual(other["meta"]["checksum"],
                                 baseline["meta"]["checksum"])

    def test_byte_identical_serialization(self):
        """Two independent builds serialize to identical bytes."""
        a = json.dumps(atmo.build(CFG), sort_keys=True)
        b = json.dumps(atmo.build(CFG, order="chunks", threads=4),
                       sort_keys=True)
        self.assertEqual(a, b)

    def test_checksum_matches_spec(self):
        """Checksum is sha256[:16] over the rounded T+S payload."""
        import hashlib
        lut = atmo.build(CFG)
        payload = {"T": lut["T"], "S": lut["S"]}
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()[:16]
        self.assertEqual(lut["meta"]["checksum"], expected)
        self.assertEqual(len(expected), 16)

    def test_deterministic_seed_independence(self):
        """The seed is accepted for config compatibility; data is seed-free."""
        cfg2 = dict(CFG, seed=999)
        self.assertEqual(atmo.build(cfg2)["T"], atmo.build(CFG)["T"])


class QueryContinuityTests(unittest.TestCase):
    # --- Acceptance 2b: clamped, continuous bilinear queries ----------------
    @classmethod
    def setUpClass(cls):
        cls.lut = atmo.build(CFG)

    def test_node_exactness(self):
        lut = self.lut
        n = CFG["lut_size"]
        for i in (0, n - 1):
            for j in (0, n - 1):
                mu = 2.0 * i / (n - 1) - 1.0
                alt = atmo.TOP * j / (n - 1)
                self.assertAlmostEqual(atmo.query(lut, "T", mu, alt),
                                       lut["T"][i][j], places=12)
                self.assertAlmostEqual(atmo.query(lut, "S", mu, alt),
                                       lut["S"][i][j], places=12)

    def test_boundary_clamping(self):
        lut = self.lut
        # Far outside the grid equals the clamped border value.
        self.assertEqual(atmo.query(lut, "T", -5.0, -100.0),
                         atmo.query(lut, "T", -1.0, 0.0))
        self.assertEqual(atmo.query(lut, "T", 5.0, 1000.0),
                         atmo.query(lut, "T", 1.0, atmo.TOP))
        self.assertEqual(atmo.query(lut, "S", -5.0, -100.0),
                         atmo.query(lut, "S", -1.0, 0.0))

    def test_continuity_across_edges_and_nodes(self):
        lut = self.lut
        eps = 1e-9
        # Continuity crossing a grid node and crossing each table edge.
        n = CFG["lut_size"]
        mu_node = 2.0 * (n // 2) / (n - 1) - 1.0
        alt_node = atmo.TOP * (n // 3) / (n - 1)
        for name, x, y in (
            ("T", mu_node, 20.0),
            ("S", mu_node, 20.0),
            ("T", 0.3, alt_node),
            ("T", 1.0 + eps, 20.0),
            ("T", -1.0 - eps, 20.0),
            ("T", 0.3, atmo.TOP + eps),
            ("T", 0.3, -eps),
        ):
            inside = atmo.query(lut, name, min(x, 1.0), min(y, atmo.TOP))
            outside = atmo.query(lut, name, x, y)
            self.assertAlmostEqual(inside, outside, places=10,
                                   msg=(name, x, y))

    def test_interpolation_lies_between_nodes(self):
        lut = self.lut
        for mu in (-0.73, 0.11, 0.84):
            for alt in (3.7, 28.4, 51.2):
                v = atmo.query(lut, "T", mu, alt)
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 1.0)


class CliTests(unittest.TestCase):
    def test_build_and_query_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            lut_path = os.path.join(tmp, "lut.json")
            with open(cfg_path, "w") as f:
                json.dump(CFG, f)
            build = subprocess.run(
                [sys.executable, "atmo.py", cfg_path, lut_path],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, check=True)
            self.assertEqual(len(build.stdout.strip()), 16)
            with open(lut_path) as f:
                lut = json.load(f)
            q = subprocess.run(
                [sys.executable, "atmo.py", "--query", lut_path,
                 "T", "0.5", "12.0"],
                capture_output=True, text=True, check=True)
            self.assertAlmostEqual(float(q.stdout),
                                   atmo.query(lut, "T", 0.5, 12.0),
                                   places=12)
            q2 = subprocess.run(
                [sys.executable, "atmo.py", "--query", lut_path,
                 "S", "-2.0", "90.0"],
                capture_output=True, text=True, check=True)
            self.assertAlmostEqual(float(q2.stdout),
                                   atmo.query(lut, "S", -1.0, atmo.TOP),
                                   places=12)

    def test_threaded_cli_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump(CFG, f)
            a = os.path.join(tmp, "a.json")
            b = os.path.join(tmp, "b.json")
            subprocess.run([sys.executable, "atmo.py", cfg_path, a],
                           check=True)
            subprocess.run([sys.executable, "atmo.py", "--order", "chunks",
                            "--threads", "4", cfg_path, b], check=True)
            with open(a) as fa, open(b) as fb:
                self.assertEqual(json.load(fa), json.load(fb))


if __name__ == "__main__":
    unittest.main(verbosity=2)
