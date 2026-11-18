#!/usr/bin/env python3
"""Acceptance tests for the adaptive deblocking filter.

Pixel-exact comparison against the fixed references in golden/ plus the
four required synthetic clips (checkerboard, step, diagonal, real hard
edge) with explicit block-artefact reduction and hard-edge retention
checks.  Run: python3 -m unittest -v test_acceptance
"""
import copy
import json
import os
import unittest

import deblock

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")
NAMES = ["checkerboard", "step", "diagonal", "hard_edge", "mask",
         "bitdepth12", "cross_schedule", "outer", "outer_edge"]


def load(name, suffix):
    with open(os.path.join(GOLDEN, name + suffix + ".json")) as f:
        return json.load(f)


def edge_map(result):
    return {(e["dir"], e["at"]): e for e in result["edges"]}


def artefact_energy(pixels, x0):
    """Mean absolute jump across a vertical boundary."""
    h = len(pixels)
    return sum(abs(pixels[y][x0] - pixels[y][x0 - 1]) for y in range(h)) / h


class DeblockAcceptance(unittest.TestCase):

    # ------------------------------------------------------------------
    # 1. Pixel-exact match with the fixed reference for every fixture.
    # ------------------------------------------------------------------
    def test_01_pixel_exact_reference(self):
        for name in NAMES:
            with self.subTest(fixture=name):
                fx = load(name, ".in")
                ref = load(name, ".ref")
                got = deblock.deblock(copy.deepcopy(fx))
                self.assertEqual(got["pixels"], ref["pixels"])
                self.assertEqual(got["filtered"], ref["filtered"])
                self.assertEqual(got["edges"], ref["edges"])

    # ------------------------------------------------------------------
    # 2. Checkerboard: a real repeating contour must not be smeared.
    # ------------------------------------------------------------------
    def test_02_checkerboard_preserved(self):
        fx = load("checkerboard", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        self.assertEqual(got["pixels"], fx["pixels"])
        self.assertEqual(got["filtered"], [])
        self.assertTrue(all(not e["filtered"] for e in got["edges"]))

    # ------------------------------------------------------------------
    # 3. Step clip: pure block artefact on x=24 is weakened; the true
    #    hard step on x=11 (off-grid) is retained exactly.
    # ------------------------------------------------------------------
    def test_03_step_clip(self):
        fx = load("step", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        em = edge_map(got)
        self.assertEqual(em[("v", 24)]["class"], "artifact")
        self.assertTrue(em[("v", 24)]["filtered"])
        before = artefact_energy(fx["pixels"], 24)
        after = artefact_energy(got["pixels"], 24)
        self.assertLess(after, before)
        # The off-grid content step at x=11 cannot be touched by any
        # grid-boundary filter: compare columns away from the x=24 stencil.
        for y in range(fx["h"]):
            for x in list(range(0, 22)) + list(range(26, fx["w"])):
                self.assertEqual(got["pixels"][y][x], fx["pixels"][y][x])

    # ------------------------------------------------------------------
    # 4. Diagonal texture crossing grid boundaries must survive.
    # ------------------------------------------------------------------
    def test_04_diagonal_preserved(self):
        fx = load("diagonal", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        self.assertEqual(got["pixels"], fx["pixels"])
        self.assertEqual(got["filtered"], [])

    # ------------------------------------------------------------------
    # 5. Hard bright/dark contour on a grid line is classified "hard"
    #    and kept, while the separate block artefact edge is smoothed.
    # ------------------------------------------------------------------
    def test_05_hard_edge_retained(self):
        fx = load("hard_edge", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        em = edge_map(got)
        self.assertEqual(em[("v", 16)]["class"], "hard")
        self.assertFalse(em[("v", 16)]["filtered"])
        # No sample in the two columns flanking x=16 may change.
        for y in range(fx["h"]):
            for x in (14, 15, 16, 17):
                self.assertEqual(got["pixels"][y][x], fx["pixels"][y][x])
        # The artefact boundary x=24 must be reduced and flagged.
        self.assertEqual(em[("v", 24)]["class"], "artifact")
        self.assertTrue(em[("v", 24)]["filtered"])
        self.assertLess(artefact_energy(got["pixels"], 24),
                        artefact_energy(fx["pixels"], 24))

    # ------------------------------------------------------------------
    # 6. Unprocessable region: masked samples never change; a boundary
    #    fully covered by the mask is reported as "blocked".
    # ------------------------------------------------------------------
    def test_06_mask_region(self):
        fx = load("mask", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        em = edge_map(got)
        self.assertEqual(em[("v", 8)]["class"], "blocked")
        self.assertFalse(em[("v", 8)]["filtered"])
        for y in range(fx["h"]):
            for x in range(fx["w"]):
                if fx["mask"][y][x]:
                    self.assertEqual(got["pixels"][y][x],
                                     fx["pixels"][y][x])

    # ------------------------------------------------------------------
    # 7. High bit depth: every output stays within the 12-bit range.
    # ------------------------------------------------------------------
    def test_07_bitdepth_saturation(self):
        fx = load("bitdepth12", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        cap = (1 << fx["bitdepth"]) - 1
        for row in got["pixels"]:
            self.assertTrue(all(0 <= v <= cap for v in row))
        self.assertTrue(any(v == cap for row in got["pixels"] for v in row))

    # ------------------------------------------------------------------
    # 8. Schedule: vertical edges are applied before horizontal edges and
    #    the first writer wins at the crossing stencil.
    # ------------------------------------------------------------------
    def test_08_v_before_h_schedule(self):
        fx = load("cross_schedule", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        em = edge_map(got)
        self.assertTrue(em[("v", 16)]["filtered"])
        self.assertTrue(em[("h", 16)]["filtered"])
        order = [e["dir"] for e in got["edges"]]
        self.assertEqual(order, ["v"] * 3 + ["h"] * 3)
        # Crossing pixel (15, 15) is in the vertical stencil first; its
        # final value must be the vertical blend of original samples.
        x0 = y0 = 16
        a = fx["pixels"][y0 - 1][x0 - 1]
        b = fx["pixels"][y0 - 1][x0]
        expected = (2 * a + 2 * b + 2) // 4
        self.assertEqual(got["pixels"][y0 - 1][x0 - 1], expected)

    # ------------------------------------------------------------------
    # 9. Image outer edge: boundaries without room for the stencil are
    #    classified "outer" and leave every sample untouched.
    # ------------------------------------------------------------------
    def test_09_outer_edge(self):
        fx = load("outer_edge", ".in")
        got = deblock.deblock(copy.deepcopy(fx))
        outer = [e for e in got["edges"] if e["class"] == "outer"]
        self.assertTrue(outer)
        self.assertTrue(all(not e["filtered"] for e in outer))
        self.assertEqual([p for row in got["pixels"] for p in row],
                         [p for row in fx["pixels"] for p in row])

    # ------------------------------------------------------------------
    # 10. The input frame (including its nested lists) is never mutated.
    # ------------------------------------------------------------------
    def test_10_input_not_mutated(self):
        for name in NAMES:
            with self.subTest(fixture=name):
                fx = load(name, ".in")
                snapshot = copy.deepcopy(fx)
                deblock.deblock(fx)
                self.assertEqual(fx, snapshot)

    # ------------------------------------------------------------------
    # 11. Per-boundary filtered flags are exported for every boundary and
    #     match the coordinate list that actually changed.
    # ------------------------------------------------------------------
    def test_11_per_boundary_flags(self):
        for name in NAMES:
            with self.subTest(fixture=name):
                fx = load(name, ".in")
                got = deblock.deblock(copy.deepcopy(fx))
                for e in got["edges"]:
                    self.assertIn("filtered", e)
                    self.assertIn("class", e)
                    self.assertIn("strength", e)
                    self.assertIsInstance(e["filtered"], bool)
                # filtered == True only on edges whose stencil changed
                # at least one sample, and every changed coordinate must
                # belong to a flagged edge's 2-pixel stencil.
                B = fx["block"]
                flagged = set()
                for e in got["edges"]:
                    if e["filtered"]:
                        at = e["at"]
                        if e["dir"] == "v":
                            for y in range(fx["h"]):
                                for x in (at - 2, at - 1, at, at + 1):
                                    flagged.add((x, y))
                        else:
                            for x in range(fx["w"]):
                                for y in (at - 2, at - 1, at, at + 1):
                                    flagged.add((x, y))
                for x, y in got["filtered"]:
                    self.assertIn((x, y), flagged)
                # Consistency with block grid coverage.
                vs = [e["at"] for e in got["edges"] if e["dir"] == "v"]
                hs = [e["at"] for e in got["edges"] if e["dir"] == "h"]
                self.assertEqual(vs, list(range(B, fx["w"], B)))
                self.assertEqual(hs, list(range(B, fx["h"], B)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
