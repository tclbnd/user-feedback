import math
import unittest

from spectral import render, run_validation, snell, trace_path


class SpectralRendererTests(unittest.TestCase):
    def test_snell_refraction_and_tir(self):
        boundary = snell(30.0, 0.0, 1.0, 1.5)
        self.assertFalse(boundary["tir"])
        self.assertAlmostEqual(
            boundary["theta_t"],
            math.degrees(math.asin(1.0 / 3.0)),
        )
        tir = snell(0.0, 45.0, 1.5, 1.0)
        self.assertTrue(tir["tir"])
        self.assertEqual(tir["fresnel"], 1.0)

    def test_pixel_sequence_is_schedule_invariant(self):
        validation = run_validation("test_reports", workers=1)
        self.assertTrue(validation["passed"])
        self.assertEqual(
            {item["passed"] for item in validation["checks"]},
            {True},
        )

    def test_path_stack_unchanged_by_tir(self):
        scene = {
            "wavelengths": [550.0],
            "xyz_basis": [[1.0, 1.0, 1.0]],
            "materials": {"glass": {"ior": [1.5], "absorb": [0.0]}},
            "rays": [{
                "angle": 0.0,
                "events": [
                    {"type": "enter", "material": "glass", "normal": 0.0},
                    {"type": "exit", "material": "glass", "normal": 45.0},
                ],
            }],
        }
        traced = trace_path(scene, scene["rays"][0], 0, deterministic=True)
        self.assertTrue(traced["tir"])
        self.assertEqual(traced["stack"], ["glass"])

    def test_linear_and_display_images_are_both_present(self):
        scene = {
            "wavelengths": [550.0],
            "xyz_basis": [[0.5, 0.5, 0.5]],
            "materials": {},
            "rays": [{"angle": 0.0, "events": [], "lights": []}],
        }
        result = render(scene)
        self.assertEqual(len(result["image_xyz"]), 1)
        self.assertEqual(len(result["image_srgb"]), 1)
        self.assertEqual(len(result["image_srgb"][0]), 3)

    def test_fresnel_and_distance_transmission_pdf_are_in_one_weight(self):
        scene = {
            "wavelengths": [550.0],
            "xyz_basis": [[1.0, 1.0, 1.0]],
            "materials": {"glass": {"ior": [1.0], "absorb": [0.2]}},
            "rays": [{
                "angle": 0.0,
                "events": [
                    {
                        "type": "enter",
                        "material": "glass",
                        "normal": 0.0,
                        "sample_fresnel": True,
                    },
                    {
                        "type": "distance",
                        "length": 2.0,
                        "sample_transmission": True,
                        "sample_rate": 0.2,
                    },
                ],
                "lights": [],
            }],
            "spp": 400,
            "seed": 9,
        }
        result = render(scene)
        ray = result["rays"][0]
        self.assertLess(ray["sampling"]["mean_joint_pdf"], 1.0)
        self.assertLess(abs(ray["stats"]["energy_relative_bias"]), 0.05)
        self.assertGreater(ray["stats"]["energy_standard_error"], 0.0)

    def test_sampled_fresnel_energy_matches_deterministic_reference(self):
        scene = {
            "wavelengths": [550.0],
            "xyz_basis": [[1.0, 1.0, 1.0]],
            "materials": {"glass": {"ior": [1.5], "absorb": [0.0]}},
            "rays": [{
                "angle": 0.0,
                "events": [
                    {
                        "type": "enter",
                        "material": "glass",
                        "normal": 0.0,
                        "sample_fresnel": True,
                    },
                ],
                "lights": [],
            }],
            "spp": 1000,
            "seed": 5,
        }
        ray = render(scene)["rays"][0]
        self.assertGreater(ray["sampling"]["mean_joint_pdf"], 0.0)
        self.assertLess(abs(ray["stats"]["energy_relative_bias"]), 0.10)
        self.assertLess(ray["stats"]["energy_relative_bias"] +
                        3.0 * ray["stats"]["energy_noise"], 0.10)


if __name__ == "__main__":
    unittest.main()
