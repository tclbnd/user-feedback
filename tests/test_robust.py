"""Acceptance 2 (extremes half): no NaN, no negatives, no exceptions.

Feeds deliberately hostile inputs: full black, denormal-level dark, a single
non-zero pixel, huge dynamic range, saturated white, colour input with black
pixels, and malformed requests.  Every output sample must be a finite float
in [0, 1] and the request must either succeed or raise ValueError.
"""
import json
import math
import os
import tempfile
import unittest

from hdr_tm import run, run_file
from tests import synth


def assert_clean(test, values, label):
    for i, v in enumerate(values):
        test.assertTrue(isinstance(v, float), "%s[%d] not a float" % (label, i))
        test.assertFalse(math.isnan(v), "%s[%d] is NaN" % (label, i))
        test.assertTrue(math.isfinite(v), "%s[%d] is inf" % (label, i))
        test.assertGreaterEqual(v, 0.0, "%s[%d] is negative" % (label, i))
        test.assertLessEqual(v, 1.0, "%s[%d] exceeds 1" % (label, i))


def flatten(img):
    return [v for row in img for v in row]


class ExtremeInputTest(unittest.TestCase):
    def _check(self, img, label, **kw):
        req = {"hdr": img, "radius": 2}
        req.update(kw)
        res = run(req)
        assert_clean(self, flatten(res["out"]), label)
        self.assertTrue(math.isfinite(res["ev"]))
        self.assertTrue(math.isfinite(res["curve"]["white_point"]))
        self.assertTrue(math.isfinite(res["curve"]["mean_log_ev"]))
        for z in res["zones"]:
            for k in ("mean_ev", "vote_ev", "contribution_ev", "weight_share"):
                self.assertTrue(math.isfinite(z[k]), "%s zone %r" % (label, k))
        return res

    def test_full_black(self):
        self._check(synth.pure_black_frame(32, 32), "full black")

    def test_full_black_single_zone(self):
        self._check(synth.pure_black_frame(32, 32), "full black, 1 zone",
                    zones=[1, 1])

    def test_denormal_dark(self):
        """Denormal level samples are far below BLACK_EPS, so they are pure
        black by definition; they must still flow through without NaN."""
        img = [[1e-30 for _ in range(16)] for _ in range(16)]
        res = self._check(img, "denormal dark")
        self.assertEqual(res["stats"]["black_pixels"], 256)
        self.assertEqual(res["stats"]["mean_source"], "black_floor")

    def test_very_dark_but_measurable(self):
        """Just above BLACK_EPS the meter must actually see the samples."""
        img = [[2e-6 for _ in range(16)] for _ in range(16)]
        res = self._check(img, "very dark")
        self.assertEqual(res["stats"]["black_pixels"], 0)
        self.assertAlmostEqual(res["stats"]["mean_log_ev"], math.log2(2e-6),
                               places=5)

    def test_mixed_pure_black_and_bright(self):
        img = [[0.0 if c < 8 else 40.0 for c in range(16)] for _ in range(16)]
        self._check(img, "black+bright")

    def test_single_nonzero_pixel(self):
        img = [[0.0] * 16 for _ in range(16)]
        img[7][7] = 0.18
        self._check(img, "single pixel")

    def test_single_bright_pixel(self):
        img = [[0.0] * 16 for _ in range(16)]
        img[7][7] = 1e6
        self._check(img, "single bright pixel")

    def test_huge_range(self):
        img = [[1e-12 * (2.0 ** c) for c in range(16)] for _ in range(16)]
        self._check(img, "huge range")

    def test_saturated_white(self):
        img = [[1e9] * 16 for _ in range(16)]
        self._check(img, "saturated white")

    def test_one_by_one_frame(self):
        res = self._check([[0.18]], "1x1")
        self.assertEqual(len(res["out"]), 1)

    def test_one_pixel_pure_black(self):
        self._check([[0.0]], "1x1 black")

    def test_single_row_frame(self):
        self._check([[0.01 * (1 + c) for c in range(16)]], "1x16")

    def test_color_with_black_pixels(self):
        img = [[[0.0, 0.0, 0.0] if c < 8 else [0.6, 0.3, 0.1]
                for c in range(16)] for _ in range(16)]
        res = self._check(img, "colour black split")
        for r in range(16):
            for c in range(16):
                px = res["rgb"][r][c]
                assert_clean(self, [float(v) for v in px], "rgb")

    def test_radius_one_and_large(self):
        img, _ = synth.exposure_ladder(h=16, w=16, bands=2)
        self._check(img, "radius 1", radius=1)
        self._check(img, "radius 20", radius=20)

    def test_tile_one(self):
        img, _ = synth.exposure_ladder(h=16, w=16, bands=2)
        self._check(img, "tile 1", tile=1)


class RequestValidationTest(unittest.TestCase):
    def test_rejects_bad_inputs(self):
        bad = [
            {},
            {"hdr": []},
            {"hdr": [[]]},
            {"hdr": [[0.1], [0.1, 0.2]]},
            {"hdr": [[0.1, "x"]]},
            {"hdr": [[0.1, None]]},
            {"hdr": [[0.1, True]]},
            {"hdr": [[float("nan")]]},
            {"hdr": [[float("inf")]]},
            {"hdr": [[0.1]], "nope": 1},
            {"hdr": [[0.1]], "max_dark_gain": 4},
            {"hdr": [[0.1]], "max_dark_gain": 0},
            {"hdr": [[0.1]], "sigma_r": 0},
            {"hdr": [[0.1]], "tile": 0},
            {"hdr": [[0.1]], "zones": [0, 2]},
            {"hdr": [[0.1]], "ev_min": 5, "ev_max": 1},
            {"hdr": [[0.1]], "gamma": 0},
        ]
        for req in bad:
            with self.assertRaises(ValueError, msg=repr(req)):
                run(req)

    def test_rejects_color_channel_count(self):
        with self.assertRaises(ValueError):
            run({"hdr": [[[0.1, 0.2]]]})
        with self.assertRaises(ValueError):
            run({"hdr": [[[0.1, 0.2, 0.3, 0.4, 0.5]]]})

    def test_accepts_one_channel_colour(self):
        res = run({"hdr": [[[0.5]]], "radius": 1})
        self.assertEqual(len(res["rgb"][0][0]), 3)


class FileIoTest(unittest.TestCase):
    def test_hdr_path_and_cli(self):
        img, _ = synth.exposure_ladder(h=16, w=16, bands=2)
        d = tempfile.mkdtemp()
        hdr_path = os.path.join(d, "i.json")
        req_path = os.path.join(d, "req.json")
        out_path = os.path.join(d, "out.json")
        with open(hdr_path, "w") as fh:
            json.dump(img, fh)
        with open(req_path, "w") as fh:
            json.dump({"hdr": "i.json", "tile": 8, "radius": 2}, fh)
        res = run_file(req_path, out_path)
        with open(out_path) as fh:
            loaded = json.load(fh)
        self.assertEqual(loaded["ev"], res["ev"])
        self.assertEqual(loaded["digest"], res["digest"])
        self.assertEqual(loaded["out"], res["out"])

    def test_output_is_strict_json(self):
        """json.dump with allow_nan=False already refuses NaN/inf."""
        res = run({"hdr": synth.pure_black_frame(8, 8), "radius": 1})
        txt = json.dumps(res, allow_nan=False)   # must not raise
        self.assertNotIn("NaN", txt)
        self.assertNotIn("Infinity", txt)


if __name__ == "__main__":
    unittest.main()