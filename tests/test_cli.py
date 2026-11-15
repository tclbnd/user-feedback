"""The documented CLI contract:  python3 tm.py req.json output.json"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests import synth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TM = os.path.join(ROOT, "tm.py")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _write(self, name, obj):
        path = os.path.join(self.dir, name)
        with open(path, "w") as fh:
            json.dump(obj, fh)
        return path

    def _invoke(self, req_path, out_path):
        return subprocess.run(
            [sys.executable, TM, req_path, out_path],
            cwd=self.dir, capture_output=True, text=True)

    def test_end_to_end(self):
        img, _ = synth.exposure_ladder(h=32, w=32, bands=4)
        self._write("i.json", img)
        req = self._write("req.json",
                          {"hdr": "i.json", "tile": 8, "radius": 2})
        out = os.path.join(self.dir, "out.json")
        proc = self._invoke(req, out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out) as fh:
            res = json.load(fh)
        # the documented output keys
        for key in ("ev", "out", "zone_ev", "digest", "stats", "zones",
                    "curve", "invariant"):
            self.assertIn(key, res)
        self.assertEqual(len(res["out"]), 32)
        self.assertEqual(len(res["out"][0]), 32)
        self.assertIsInstance(res["ev"], float)

    def test_inline_hdr_array(self):
        img, _ = synth.exposure_ladder(h=16, w=16, bands=2)
        req = self._write("req.json", {"hdr": img, "radius": 1})
        out = os.path.join(self.dir, "o.json")
        proc = self._invoke(req, out)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_deterministic_across_processes(self):
        img, _ = synth.exposure_ladder(h=32, w=32, bands=4)
        self._write("i.json", img)
        req = self._write("req.json", {"hdr": "i.json", "tile": 8,
                                       "radius": 2})
        outs = []
        for k in range(2):
            out = os.path.join(self.dir, "o%d.json" % k)
            proc = self._invoke(req, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as fh:
                outs.append(json.load(fh))
        self.assertEqual(outs[0]["ev"], outs[1]["ev"])
        self.assertEqual(outs[0]["digest"], outs[1]["digest"])
        self.assertEqual(outs[0]["curve"], outs[1]["curve"])

    def test_tiling_invariance_through_cli(self):
        """Same HDR, two tilings, via the real CLI path -> same parameters."""
        img, _ = synth.exposure_ladder(h=48, w=48, bands=6)
        self._write("i.json", img)
        results = []
        for k, tile in enumerate((4, 24)):
            req = self._write("req%d.json" % k,
                              {"hdr": "i.json", "tile": tile, "radius": 2})
            out = os.path.join(self.dir, "o%d.json" % k)
            proc = self._invoke(req, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as fh:
                results.append(json.load(fh))
        self.assertEqual(results[0]["ev"], results[1]["ev"])
        self.assertEqual(results[0]["curve"], results[1]["curve"])
        self.assertEqual(results[0]["digest"], results[1]["digest"])
        self.assertEqual(results[0]["invariant"], results[1]["invariant"])

    def test_bad_request_exits_nonzero(self):
        req = self._write("bad.json", {"hdr": [[0.1]], "nope": 1})
        out = os.path.join(self.dir, "o.json")
        proc = self._invoke(req, out)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nope", proc.stderr)

    def test_usage_without_arguments(self):
        proc = subprocess.run([sys.executable, TM], cwd=self.dir,
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("usage", (proc.stdout + proc.stderr).lower())


if __name__ == "__main__":
    unittest.main()