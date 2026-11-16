#!/usr/bin/env python3
"""Tests for deblock.py (fixtures are generated inline)."""
import copy
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

import deblock

HERE = os.path.dirname(os.path.abspath(__file__))


def grid_from(rows):
    return [list(row) for row in rows]


def blockiness(px, block, axis):
    """Mean absolute jump across every interior block boundary."""
    h, w = len(px), len(px[0])
    total = 0.0
    count = 0
    if axis == "v":
        for x0 in range(block, w, block):
            for y in range(h):
                total += abs(px[y][x0] - px[y][x0 - 1])
                count += 1
    else:
        for y0 in range(block, h, block):
            for x in range(w):
                total += abs(px[y0][x] - px[y0 - 1][x])
                count += 1
    return total / count if count else 0.0


def run(px, block=8, strength=20, protect=1000, **extra):
    d = {"w": len(px[0]), "h": len(px), "pixels": px,
         "block": block, "strength": strength, "protect": protect}
    d.update(extra)
    return deblock.deblock(d)


class Fixtures(unittest.TestCase):
    def test_checkerboard_block_edges_are_detected_and_smoothed(self):
        # Classic block checkerboard: 8x8 tiles alternating 0/255.  The step
        # across a boundary flips sign from one block row to the next, so a
        # whole-line signed sum cancels to zero and misses every edge; the
        # per-line activity must still see it.
        px = grid_from([[0 if ((x // 8) + (y // 8)) % 2 == 0 else 255
                         for x in range(32)] for y in range(32)])
        out = run(px, block=8, strength=20, protect=1000)
        # vertical pass sees the original frame; horizontal pass runs on the
        # vertically filtered frame (the specified schedule), so its activity
        # is lower but still far above strength.
        v_edges = [e for e in out["edges"] if e["dir"] == "v"]
        h_edges = [e for e in out["edges"] if e["dir"] == "h"]
        self.assertTrue(all(e["A"] == 255.0 for e in v_edges))
        self.assertTrue(all(e["A"] > 100.0 for e in h_edges))
        self.assertTrue(all(e["filtered"] for e in out["edges"]))
        self.assertLess(blockiness(out["pixels"], 8, "v"),
                        blockiness(px, 8, "v"))
        self.assertLess(blockiness(out["pixels"], 8, "h"),
                        blockiness(px, 8, "h"))
        # every reported pixel really changed, and nothing else did
        changed = {(x, y) for x, y in out["filtered"]}
        actual = {(x, y) for y in range(32) for x in range(32)
                  if out["pixels"][y][x] != px[y][x]}
        self.assertEqual(changed, actual)
        self.assertEqual(out["filtered"], sorted(out["filtered"],
                                                 key=lambda c: (c[1], c[0])))

    def test_step_detected_and_ribbon_limited(self):
        px = grid_from([[40 if x < 16 else 200 for x in range(32)]
                        for y in range(16)])
        out = run(px)
        edge = [e for e in out["edges"] if e["dir"] == "v" and e["at"] == 16]
        self.assertEqual(len(edge), 1)
        self.assertAlmostEqual(edge[0]["A"], 160.0, places=1)
        self.assertTrue(edge[0]["filtered"])
        # nothing further than 2 px from the edge is touched
        for y in range(16):
            self.assertEqual(out["pixels"][y][:14], px[y][:14])
            self.assertEqual(out["pixels"][y][19:], px[y][19:])
    def test_dark_boundary_has_same_activity_as_bright(self):
        dark = grid_from([[10 if x < 8 else 60 for x in range(16)]
                          for y in range(8)])
        bright = grid_from([[150 if x < 8 else 200 for x in range(16)]
                            for y in range(8)])
        a_dark = [e for e in run(dark)["edges"] if e["dir"] == "v"]
        a_bright = [e for e in run(bright)["edges"] if e["dir"] == "v"]
        self.assertEqual([e["A"] for e in a_dark], [50.0])
        self.assertEqual([e["A"] for e in a_bright], [50.0])
        self.assertTrue(all(e["filtered"] for e in a_dark + a_bright))

    def test_real_hard_edge_on_a_block_seam_is_protected(self):
        # A genuine contour that happens to coincide with the block seam at
        # x=8: flat 60 to the left, flat 220 to the right.  Every inward
        # gradient is zero, so only the across-edge step can recognise it.
        px = grid_from([[60 if x < 8 else 220 for x in range(24)]
                        for y in range(16)])
        out = run(px, protect=30)
        self.assertEqual(out["pixels"], px)     # contour and flats untouched
        seam = [e for e in out["edges"] if e["dir"] == "v" and e["at"] == 8]
        self.assertEqual(len(seam), 1)
        self.assertFalse(seam[0]["filtered"])   # kept as real detail
        self.assertGreaterEqual(seam[0]["A"], 20)   # ... despite the activity
        self.assertEqual(out["filtered"], [])   # nothing changed
        # with protect above the step the same seam IS blended, proving the
        # guard (not luck) is what preserves the contour
        loose = run(px, protect=1000)
        self.assertNotEqual(loose["pixels"], px)
        self.assertLess(abs(loose["pixels"][0][8] - loose["pixels"][0][7]), 160)

    def test_diagonal_texture_keeps_its_contrast(self):
        # Hard diagonal contour (period 8) crossing every block boundary.
        px = grid_from([[255 if (x + y) % 8 < 4 else 0 for x in range(32)]
                        for y in range(32)])
        out = run(px, protect=8)
        far = [(x, y) for y in range(32) for x in range(32)
               if x % 8 not in (0, 1, 6, 7) and y % 8 not in (0, 1, 6, 7)]
        for x, y in far:
            self.assertEqual(out["pixels"][y][x], px[y][x])
        # the steep steps of the texture still exist after filtering
        steps = max(abs(out["pixels"][y][x + 1] - out["pixels"][y][x])
                    for y in range(32) for x in range(31))
        self.assertGreaterEqual(steps, 200)

    def test_rim_is_never_read_out_of_bounds(self):
        # w/h are not multiples of the block, so the last boundary is the rim
        px = grid_from([[20 + 3 * x + 5 * y for x in range(19)]
                        for y in range(13)])
        out = run(px, block=6, strength=1, protect=1000)
        self.assertEqual([e["at"] for e in out["edges"] if e["dir"] == "h"],
                         [6, 12])
        self.assertEqual([e["at"] for e in out["edges"] if e["dir"] == "v"],
                         [6, 12, 18])
        for edge in out["edges"]:
            self.assertIn("filtered", edge)
            self.assertIsInstance(edge["filtered"], bool)
        self.assertEqual(len(out["pixels"]), 13)
        self.assertTrue(all(len(row) == 19 for row in out["pixels"]))

    def test_high_bit_depth_saturates(self):
        px = grid_from([[4000 if x < 8 else 4095 for x in range(16)]
                        for y in range(8)])
        out = run(px, strength=10, bits=12)
        self.assertTrue(all(0 <= v <= 4095 for row in out["pixels"]
                            for v in row))
        # inferring the ceiling from the samples also keeps data in range
        out2 = run(px, strength=10)
        self.assertTrue(all(0 <= v <= 4095 for row in out2["pixels"]
                            for v in row))

    def test_input_is_not_modified(self):
        px = grid_from([[random.Random(x * 31 + y).randrange(256)
                         for x in range(16)] for y in range(16)])
        snapshot = copy.deepcopy(px)
        d = {"w": 16, "h": 16, "pixels": px, "block": 8,
             "strength": 5, "protect": 1000}
        deblock.deblock(d)
        self.assertEqual(px, snapshot)
        self.assertEqual(d["pixels"], snapshot)

    def test_flat_frame_is_reported_but_not_flagged(self):
        px = grid_from([[100] * 16 for _ in range(16)])
        out = run(px, strength=1, protect=1000)
        self.assertEqual(out["pixels"], px)
        self.assertEqual(out["filtered"], [])
        self.assertEqual([e["A"] for e in out["edges"]], [0.0, 0.0])
        self.assertFalse(any(e["filtered"] for e in out["edges"]))

    def test_unprocessable_edges_are_not_flagged(self):
        # A boundary with no usable taps cannot be filtered even at strength 0.
        px = grid_from([[5 + x for x in range(8)] for _ in range(8)])
        out = run(px, block=8, strength=0, protect=1000)
        self.assertEqual(out["edges"], [])  # no interior boundary at all

    def test_fixed_reference_pixels(self):
        # One vertical boundary at x=4, a step 0 -> 100.  Hand-computed with
        # A = |mean(0,0) - mean(100,100)| = 100, protect disabled:
        #   x=3 (near left)  = (2*0   + 100 + 2)//4 = 25
        #   x=4 (near right) = (2*100 + 0   + 2)//4 = 50
        #   x=2 (far left)   = (3*0   + 100 + 2)//4 = 25
        #   x=5 (far right)  = (3*100 + 0   + 2)//4 = 75
        # Everything else is untouched; h=4 leaves no horizontal boundary.
        px = grid_from([[0, 0, 0, 0, 100, 100, 100, 100] for _ in range(4)])
        out = run(px, block=4, strength=20, protect=1000)
        expected = [0, 0, 25, 25, 50, 75, 100, 100]
        for row in out["pixels"]:
            self.assertEqual(row, expected)
        self.assertEqual(out["edges"],
                         [{"at": 4, "dir": "v", "A": 100.0,
                           "filtered": True}])
        self.assertEqual(sorted(out["filtered"]),
                         sorted([[x, y] for y in range(4) for x in (2, 3, 4, 5)]))

    def test_blocking_reduced_while_hard_edges_kept(self):
        # One clip containing both problems: a quantised smooth ramp with
        # per-block DC residue (blocking) plus a hard-edged bright rectangle
        # whose borders sit on and off block seams (real detail).
        def clip():
            rng = random.Random(7)
            rows = []
            for y in range(32):
                row = []
                for x in range(48):
                    v = 40 + 2 * x + y // 2
                    v += (((x // 8) * 8 + (y // 8) * 6) % 17) - 8   # residue
                    v += rng.randint(-1, 1)
                    if 24 <= x < 32 and 8 <= y < 24:
                        v = 235                    # hard-edged object
                    row.append(max(0, min(255, v)))
                rows.append(row)
            return rows

        px = grid_from(clip())
        out = run(px, strength=4, protect=40)
        self.assertLess(blockiness(out["pixels"], 8, "v"),
                        blockiness(px, 8, "v"))
        # the object keeps its full contrast on both vertical borders
        self.assertGreaterEqual(abs(out["pixels"][10][24]
                                    - out["pixels"][10][23]), 130)
        self.assertGreaterEqual(abs(out["pixels"][10][32]
                                    - out["pixels"][10][31]), 130)
        # and its interior is untouched
        for y in range(9, 23):
            self.assertEqual(out["pixels"][y][26:30], px[y][26:30])

    def test_qp_only_fixture_classifies_blocking(self):
        # A fixture that exposes only qp: block steps around qp are smoothed,
        # a much larger step at the same kind of seam is kept.
        small = grid_from([[100 if x < 8 else 112 for x in range(16)]
                           for _ in range(8)])
        out = deblock.deblock({"w": 16, "h": 8, "pixels": small,
                               "block": 8, "qp": 10, "protect": 40})
        self.assertNotEqual(out["pixels"], small)     # blocking smoothed
        hard = grid_from([[40 if x < 8 else 210 for x in range(16)]
                          for _ in range(8)])
        out = deblock.deblock({"w": 16, "h": 8, "pixels": hard,
                               "block": 8, "qp": 10, "protect": 40})
        self.assertEqual(out["pixels"], hard)         # real edge kept
        self.assertEqual(out["filtered"], [])

    def test_schedule_vertical_before_horizontal(self):
        px = grid_from([[0 if ((x // 8) + (y // 8)) % 2 == 0 else 255
                         for x in range(24)] for y in range(24)])
        out = run(px, block=8, strength=20, protect=1000)
        dirs = [e["dir"] for e in out["edges"]]
        self.assertEqual(dirs, ["v"] * 2 + ["h"] * 2)
        self.assertEqual([e["at"] for e in out["edges"]], [8, 16, 8, 16])

    def test_qp_derived_strength(self):
        # A small blocking step passes with a coarse qp but is refused when an
        # explicit, larger strength is demanded.
        px = grid_from([[100, 100, 100, 100, 112, 112, 112, 112]
                        for _ in range(4)])
        base = {"w": 8, "h": 4, "pixels": px, "block": 4, "protect": 1000}
        # A 12-level step is within the quantiser step qp=10 -> artifact.
        out_coarse = deblock.deblock(dict(base, qp=10))
        self.assertTrue(out_coarse["edges"][0]["filtered"])
        # A stricter explicit threshold rejects the same step as real detail.
        out_strict = deblock.deblock(dict(base, strength=40))
        self.assertFalse(out_strict["edges"][0]["filtered"])
        self.assertEqual(out_strict["pixels"], px)
        # the qp-derived threshold scales with bit depth
        self.assertEqual(deblock._strength({"qp": 20}, 255), 20.0)
        self.assertAlmostEqual(deblock._strength({"qp": 20}, 1023), 80.0)

    def test_bad_input_is_rejected(self):
        cases = [
            {"w": 4, "h": 2, "pixels": [[0] * 4], "block": 4,
             "strength": 1, "protect": 1},                    # wrong row count
            {"w": 4, "h": 1, "pixels": [[0] * 3], "block": 4,
             "strength": 1, "protect": 1},                    # short row
            {"w": 4, "h": 1, "pixels": [[0] * 4], "block": 0,
             "strength": 1, "protect": 1},                    # bad block
            {"w": 4, "h": 1, "pixels": [[0] * 4], "block": 4,
             "strength": 1, "protect": -1},                   # bad protect
            {"w": 4, "h": 1, "pixels": [[0] * 4], "block": 4,
             "protect": 1},                                   # no strength
        ]
        for doc in cases:
            with self.assertRaises((ValueError, TypeError)):
                deblock.deblock(doc)

    def test_cli_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            with open(src, "w") as handle:
                json.dump({"w": 4, "h": 1, "pixels": [[0] * 4],
                           "block": 0, "strength": 1, "protect": 1}, handle)
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "deblock.py"), src, dst],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("invalid input", proc.stderr)
            self.assertFalse(os.path.exists(dst))

    def test_cli_end_to_end(self):
        px = grid_from([[40 if x < 8 else 200 for x in range(16)]
                        for y in range(8)])
        doc = {"w": 16, "h": 8, "pixels": px, "block": 8,
               "strength": 20, "protect": 1000}
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.json")
            dst = os.path.join(tmp, "out.json")
            with open(src, "w") as handle:
                json.dump(doc, handle)
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "deblock.py"), src, dst],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(dst) as handle:
                written = json.load(handle)
            with open(src) as handle:
                reread = json.load(handle)
        # the input file on disk is byte-identical content-wise
        self.assertEqual(reread, doc)
        self.assertEqual(written["pixels"], deblock.deblock(doc)["pixels"])
        self.assertEqual(set(written), {"pixels", "filtered", "edges"})
        self.assertTrue(written["edges"][0]["filtered"])
        self.assertEqual(written["filtered"],
                         sorted(written["filtered"], key=lambda c: (c[1], c[0])))

    def test_cli_usage_error(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "deblock.py")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr)


if __name__ == "__main__":
    unittest.main()
