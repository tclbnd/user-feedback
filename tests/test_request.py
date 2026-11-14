"""Request/response contract, warnings, tracing and CLI entry point."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from yuvkit.checks import compare_constants, explain
from yuvkit.request import RequestError, run_request


def base_request(**kwargs):
    req = {
        "standard": "bt709",
        "rgb_range": "full",
        "yuv_range": "limited",
        "ops": [{"direction": "rgb-to-yuv",
                 "pixels": [[0, 0, 0], [255, 255, 255], [255, 0, 0]]}],
    }
    req.update(kwargs)
    return req


class TestBasics(unittest.TestCase):
    def test_simple_conversion(self):
        res = run_request(base_request())
        self.assertEqual(res["ops"][0]["pixels"][0], [16, 128, 128])
        self.assertEqual(res["ops"][0]["pixels"][2], [63, 102, 240])
        self.assertEqual(res["warnings"], [])

    def test_response_shape_is_stable(self):
        res = run_request(base_request())
        self.assertIn("ops", res)
        self.assertIn("warnings", res)
        self.assertIn("range_tags", res["ops"][0])
        self.assertIn("conventions", res)

    def test_inverse_direction(self):
        req = base_request(ops=[{"direction": "yuv-to-rgb",
                                 "pixels": [[16, 128, 128]]}])
        res = run_request(req)
        self.assertEqual(res["ops"][0]["pixels"][0], [0, 0, 0])

    def test_json_roundtrip(self):
        res = run_request(base_request())
        self.assertEqual(json.loads(json.dumps(res)), res)


class TestValidation(unittest.TestCase):
    def test_missing_ops(self):
        with self.assertRaises(RequestError):
            run_request({"standard": "bt709"})

    def test_empty_ops(self):
        with self.assertRaises(RequestError):
            run_request({"standard": "bt709", "ops": []})

    def test_bad_direction(self):
        with self.assertRaises(RequestError):
            run_request(base_request(ops=[{"direction": "sideways",
                                           "pixels": [[1, 2, 3]]}]))

    def test_bad_pixel(self):
        with self.assertRaises(RequestError):
            run_request(base_request(ops=[{"direction": "rgb-to-yuv",
                                           "pixels": [[1, 2]]}]))

    def test_bad_standard(self):
        with self.assertRaises(RequestError):
            run_request(base_request(standard="bt470"))

    def test_bad_layout(self):
        with self.assertRaises(RequestError):
            run_request(base_request(chroma_layout="4:1:1"))

    def test_bad_path(self):
        with self.assertRaises(RequestError):
            run_request(base_request(path="quantum"))

    def test_out_of_range_code_is_clamped_and_flagged(self):
        # The request layer clamps and warns; the strict behaviour lives
        # in Converter.convert (clamp=False) and is tested there.
        res = run_request(base_request(ops=[{"direction": "rgb-to-yuv",
                                             "pixels": [[300, 0, 0]]}]))
        self.assertIn("range-mismatch", res["warnings"])
        self.assertEqual(res["ops"][0]["pixels"][0], [63, 102, 240])


class TestWarnings(unittest.TestCase):
    def test_meta_standard_mismatch(self):
        res = run_request(base_request(meta={"standard": "bt601"}))
        self.assertIn("mixed-standard", res["warnings"])

    def test_meta_standard_agreement_is_silent(self):
        res = run_request(base_request(meta={"standard": "bt709"}))
        self.assertEqual(res["warnings"], [])

    def test_chain_mixing_standards(self):
        res = run_request(base_request(chain=[
            {"standard": "bt601", "direction": "rgb-to-yuv"},
            {"standard": "bt709", "direction": "yuv-to-rgb"},
        ]))
        self.assertIn("mixed-standard", res["warnings"])

    def test_chain_mixing_ranges(self):
        res = run_request(base_request(chain=[
            {"standard": "bt709", "yuv_range": "limited"},
            {"standard": "bt709", "yuv_range": "full"},
        ]))
        self.assertIn("mixed-standard", res["warnings"])

    def test_per_op_standard_override(self):
        res = run_request(base_request(ops=[
            {"direction": "rgb-to-yuv", "standard": "bt601",
             "pixels": [[1, 2, 3]]},
            {"direction": "rgb-to-yuv", "standard": "bt709",
             "pixels": [[4, 5, 6]]},
        ]))
        self.assertIn("mixed-standard", res["warnings"])

    def test_range_mismatch_detected_on_rgb_side(self):
        # Full-range RGB codes under a limited declaration.
        res = run_request({
            "standard": "bt709", "rgb_range": "limited",
            "yuv_range": "limited",
            "ops": [{"direction": "rgb-to-yuv",
                     "pixels": [[0, 0, 0], [255, 255, 255]]}],
        })
        self.assertIn("range-mismatch", res["warnings"])
        # clamped, not dropped
        self.assertEqual(res["ops"][0]["pixels"][0], [16, 128, 128])

    def test_full_range_yuv_in_limited_declaration(self):
        res = run_request({
            "standard": "bt709", "rgb_range": "full",
            "yuv_range": "limited",
            "ops": [{"direction": "yuv-to-rgb", "pixels": [[0, 0, 0]]}],
        })
        self.assertIn("range-mismatch", res["warnings"])

    def test_every_warning_is_explained(self):
        tokens = ["mixed-standard", "mixed-range", "range-mismatch",
                  "clipping", "roundtrip-over-tolerance"]
        for text in explain(tokens):
            self.assertNotEqual(text, "")

    def test_constants_diagnostic(self):
        info = compare_constants(["bt601", "bt709"])
        self.assertIn("bt601", info["constants"])
        self.assertLess(info["deltas"]["bt709-vs-bt601"]["delta_kr"], 0)


class TestRoundtripField(unittest.TestCase):
    def test_roundtrip_block(self):
        res = run_request(base_request(roundtrip=True))
        rt = res["ops"][0]["roundtrip"]
        self.assertLessEqual(rt["max_err"], rt["tolerance"])
        self.assertTrue(rt["ok"])
        self.assertEqual(rt["tolerance"], 2.0)

    def test_roundtrip_with_per_op_standard_is_still_in_tolerance(self):
        req = base_request(roundtrip=True, ops=[
            {"direction": "rgb-to-yuv", "standard": "bt601",
             "pixels": [[200, 40, 90]]}])
        res = run_request(req)
        self.assertLessEqual(res["ops"][0]["roundtrip"]["max_err"], 2)
        # ...but the mixed declaration is still called out
        self.assertIn("mixed-standard", res["warnings"])

    def test_clipped_list_is_empty_for_clean_pixels(self):
        res = run_request(base_request(roundtrip=True))
        self.assertEqual(res["ops"][0]["roundtrip"]["clipped"], [])


class TestTracingField(unittest.TestCase):
    def test_trace_records(self):
        res = run_request(base_request(trace=True))
        records = res["trace"]["records"]
        self.assertEqual(len(records), 3)
        rec = records[0]
        self.assertEqual(rec["src"], [0, 0, 0])
        self.assertEqual(rec["dst"], [16, 128, 128])
        self.assertEqual(rec["tags"]["yuv_range"], "limited")
        self.assertEqual(rec["op"], 0)

    def test_trace_limit(self):
        res = run_request(base_request(trace=True, trace_limit=2))
        self.assertEqual(len(res["trace"]["records"]), 2)

    def test_no_trace_by_default(self):
        res = run_request(base_request())
        self.assertNotIn("trace", res)


class TestPathField(unittest.TestCase):
    def test_both_paths_report_consistency(self):
        res = run_request(base_request(path="both"))
        self.assertTrue(res["consistency"]["identical"])
        self.assertEqual(res["consistency"]["max_delta"], 0)

    def test_lut_output_matches_matrix(self):
        a = run_request(base_request())
        b = run_request(base_request(path="lut"))
        self.assertEqual(a["ops"][0]["pixels"], b["ops"][0]["pixels"])


class TestCli(unittest.TestCase):
    def _run(self, *args):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, os.path.join(root,
                                                            "convert.py")]
                              + list(args), capture_output=True, text=True)

    def test_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            with open(src, "w") as fh:
                json.dump(base_request(roundtrip=True), fh)
            proc = self._run(src, dst)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(dst) as fh:
                out = json.load(fh)
            self.assertEqual(out["ops"][0]["pixels"][0], [16, 128, 128])

    def test_warning_goes_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            req = base_request(meta={"standard": "bt601"})
            with open(src, "w") as fh:
                json.dump(req, fh)
            proc = self._run(src, dst)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("mixed-standard", proc.stderr)

    def test_invalid_request_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            with open(src, "w") as fh:
                fh.write("{}")
            proc = self._run(src, dst)
            self.assertEqual(proc.returncode, 3)
            self.assertIn("invalid request", proc.stderr)

    def test_help(self):
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("convert.py", proc.stdout)

    def test_diagnose(self):
        proc = self._run("--diagnose", "bt601", "bt709")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("delta_kr", proc.stdout)


class TestOriginalBugFixed(unittest.TestCase):
    """Explicit regression guard for the three defects in the report."""

    def test_709_primaries_use_709_constants(self):
        res = run_request(base_request())
        self.assertEqual(res["ops"][0]["pixels"][2], [63, 102, 240])
        # BT.601 would give (81, 90, 240) for red
        self.assertNotEqual(res["ops"][0]["pixels"][2], [81, 90, 240])

    def test_chroma_gain_sign(self):
        # 0.5/(1+Kb) instead of 0.5/(1-Kb) would put blue's Cb near 230.
        res = run_request(base_request(ops=[
            {"direction": "rgb-to-yuv", "pixels": [[0, 0, 255]]}]))
        self.assertEqual(res["ops"][0]["pixels"][0][1], 240)

    def test_range_scaling_after_matrix(self):
        # Grey with the old code produced chroma far from 128.
        pixels = [[v, v, v] for v in range(0, 256, 5)]
        res = run_request(base_request(ops=[{"direction": "rgb-to-yuv",
                                             "pixels": pixels}]))
        for yuv in res["ops"][0]["pixels"]:
            self.assertEqual(yuv[1:], [128, 128])


if __name__ == "__main__":
    unittest.main()

