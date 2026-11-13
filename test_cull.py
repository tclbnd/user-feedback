"""Independent acceptance tests for conservative occlusion culling.

The ground truth is always the full, no-cull rasteriser's per-pixel instance
ID image (``cull.reference_render``).  A culled frame passes only if its
pixels are exactly equal to the baseline, i.e. known fully-occluded instances
are removed while no visible instance is ever dropped.
"""
import copy
import math
import random
import unittest

import cull

W, H, NEAR = 32, 24, 0.25


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def full_render(frame):
    ids = {i["id"] for i in frame["instances"]}
    return cull.reference_render(ids, frame["instances"], frame, W, H, NEAR)


def cull_render(frame, **kw):
    result, _ = cull.cull_frame(frame, W, H, NEAR, **kw)
    return result


def assert_matches_ground_truth(testcase, frame, result,
                                expect_cull=False):
    ground = full_render(frame)
    visible_ids = {pid for row in ground for pid in row if pid is not None}
    kept = set(result["kept"])

    # Zero false removals: every instance that paints a baseline pixel stays.
    testcase.assertTrue(
        visible_ids.issubset(kept),
        "false cull: visible %s missing from kept %s"
        % (sorted(visible_ids - kept), result["kept"]))
    # Culled instances must truly be invisible in the baseline image.
    culled = {i["id"] for i in frame["instances"]} - kept
    testcase.assertTrue(culled.isdisjoint(visible_ids))
    # The culled draw must reproduce every baseline pixel exactly.
    testcase.assertEqual(result["ids"], ground)
    if expect_cull:
        testcase.assertTrue(culled, "expected at least one culled instance")
    return culled, visible_ids


def naive_depth_range_cull(frame):
    """The deliberately coarse baseline: one global depth range per frame."""
    nearest_occ = min((o["z"] for o in frame["occluders"]
                       if o["opaque"]), default=math.inf)
    return {i["id"] for i in frame["instances"]
            if i["box"][2] <= nearest_occ or i["box"][5] < NEAR}


def occluder(rect, z, opaque=True):
    return {"rect": list(rect), "z": z, "opaque": opaque}


def inst(iid, material, box):
    return {"id": iid, "material": material, "box": list(box)}


# --------------------------------------------------------------------------- #
# Unit-level conservative primitives
# --------------------------------------------------------------------------- #
class PrimitivesTest(unittest.TestCase):
    def test_near_piercing_box_is_kept(self):
        kind, _, _ = cull.classify_box(
            [-1.0, -1.0, 0.10, 1.0, 1.0, 5.0], W, H, NEAR)
        self.assertEqual(kind, "near_piercing")

    def test_box_fully_behind_near_is_culled_without_query(self):
        kind, _, _ = cull.classify_box(
            [-1.0, -1.0, 0.05, 1.0, 1.0, 0.20], W, H, NEAR)
        self.assertEqual(kind, "behind_near")

    def test_partial_offscreen_projection_is_kept_and_clamped(self):
        # Thin slab hugging the right edge: footprint extends past x == W.
        kind, rect, _ = cull.classify_box(
            [0.90, -0.1, 1.0, 1.20, 0.1, 1.2], W, H, NEAR)
        self.assertEqual(kind, "query")
        self.assertEqual(rect[2], W)  # clamped to screen, never dropped
        self.assertLess(rect[0], W)

    def test_fully_offscreen_box_is_dropped(self):
        kind, _, _ = cull.classify_box(
            [2.5, 0.0, 1.0, 2.6, 0.1, 1.2], W, H, NEAR)
        self.assertEqual(kind, "offscreen")

    def test_pyramid_marks_partially_covered_tiles_infinite(self):
        frame = {"occluders": [occluder([0, 0, 3, 1], 1.0)],
                 "instances": []}
        pyramid, dims = cull.build_depth_pyramid(frame, W, H)
        self.assertEqual(pyramid[0][0], 1.0)
        self.assertEqual(pyramid[0][3], math.inf)
        # Level-1 tile (0,0) spans four level-0 pixels, one uncovered.
        self.assertEqual(pyramid[1][0], math.inf)
        # A fully covered 2x2 region reduces to its farthest depth.
        full = {"occluders": [occluder([0, 0, 2, 2], 1.0)],
                "instances": []}
        pyr2, _ = cull.build_depth_pyramid(full, W, H)
        self.assertEqual(pyr2[1][0], 1.0)

    def test_equal_depth_is_not_occlusion(self):
        frame = {"occluders": [occluder([0, 0, W, H], 2.0)],
                 "instances": [inst(1, "m", [-0.5, -0.5, 2.0, 0.5, 0.5, 2.2])]}
        result = cull_render(frame)
        self.assertIn(1, result["kept"])  # strict farther-than test required

    def test_transparent_occluder_has_no_depth_eligibility(self):
        frame = {"occluders": [occluder([0, 0, W, H], 0.5, opaque=False)],
                 "instances": [inst(1, "m", [-0.5, -0.5, 3.0, 0.5, 0.5, 3.2])]}
        result = cull_render(frame)
        self.assertEqual(result["kept"], [1])
        self.assertEqual(result["stats"]["reasons"]["occluded"], 0)


# --------------------------------------------------------------------------- #
# The three required motion sequences
# --------------------------------------------------------------------------- #
class NearPlaneSequenceTest(unittest.TestCase):
    def build(self):
        frames = []
        # A slab animates from safely in front, through the near plane, to
        # fully behind it, while an opaque wall sits behind the slab.
        for t in range(6):
            z0 = 0.40 - 0.12 * t          # 0.40 .. -0.20
            z1 = z0 + 0.30
            frames.append({
                "occluders": [occluder([0, 0, W, H], 4.0)],
                "instances": [inst(1, "slab", [-0.6, -0.6, z0, 0.6, 0.6, z1])]})
        return frames

    def test_conservative_through_near_plane(self):
        for frame in self.build():
            result = cull_render(frame)
            assert_matches_ground_truth(self, frame, result)
            piercing = frame["instances"][0]["box"][2] < NEAR
            if piercing and frame["instances"][0]["box"][5] >= NEAR:
                self.assertIn(1, result["kept"])
                self.assertEqual(
                    result["stats"]["reasons"]["near_piercing"], 1)


class ThinOccluderSequenceTest(unittest.TestCase):
    def build(self):
        frames = []
        for shift in range(4):
            # Two tiny opaque plates with a visible gap between them.
            x = 4 + shift
            frames.append({
                "occluders": [
                    occluder([x, 10, x + 3, 13], 2.0),
                    occluder([x + 6, 10, x + 9, 13], 2.0)],
                "instances": [
                    # Wide background slab spanning both plates and the gap;
                    # the gap pixels guarantee it stays visible.
                    inst(1, "back", [-0.4, -0.2, 3.0, 0.4, 0.2, 3.2]),
                    # A tall thin slab whose nearest face is in front of the
                    # plates while its far side extends behind them: a pure
                    # depth-range comparison deletes it, the hierarchy keeps it.
                    inst(2, "edge", [-0.12, -0.2, 1.8, -0.05, 0.2, 2.6])]})
        return frames

    def test_conservative_keeps_thin_edges(self):
        naive_failed = False
        for frame in self.build():
            result = cull_render(frame)
            culled, visible = assert_matches_ground_truth(self, frame, result)
            # Both slabs touch uncovered pixels; the hierarchy must keep them.
            self.assertIn(1, result["kept"])
            self.assertIn(2, result["kept"])
            # Demonstrates why the simple depth-range heuristic is wrong.
            if not visible.issubset(naive_depth_range_cull(frame)):
                naive_failed = True
        self.assertTrue(naive_failed,
                        "naive depth-range cull unexpectedly stayed correct")


class FastMotionSequenceTest(unittest.TestCase):
    def build(self):
        frames = []
        positions = [(2, 4), (20, 18), (6, 20), (24, 4), (2, 4), (20, 18)]
        for t, (ox, ix) in enumerate(positions):
            frames.append({
                "occluders": [occluder([ox, 6, ox + 8, 16], 1.5)],
                "instances": [
                    inst(1, "a", [-0.5 + 0.05 * ix, -0.4, 2.5,
                                  0.5 + 0.05 * ix, 0.4, 2.7]),
                    inst(2, "b", [0.6, 0.5, 2.5, 0.9, 0.8, 2.7])]})
        return frames

    def test_each_frame_recomputes_from_current_evidence(self):
        frames = self.build()
        prev = None
        for idx, frame in enumerate(frames):
            result, hint = cull.cull_frame(
                frame, W, H, NEAR, workers=3, previous=prev)
            assert_matches_ground_truth(self, frame, result)
            if idx > 0:
                # Moving geometry invalidates the previous-frame hint.
                self.assertTrue(result["stats"]["fallback_recompute"])
                self.assertFalse(result["stats"]["reused_previous"])
            prev = hint

        # An unchanged frame must reuse the matching previous hint verbatim.
        same = copy.deepcopy(frames[0])
        first, hint0 = cull.cull_frame(same, W, H, NEAR, workers=2)
        again, _ = cull.cull_frame(
            copy.deepcopy(same), W, H, NEAR, workers=2,
            previous=copy.deepcopy(hint0))
        self.assertTrue(again["stats"]["reused_previous"])
        self.assertEqual(again["kept"], first["kept"])
        self.assertEqual(again["batches"], first["batches"])


# --------------------------------------------------------------------------- #
# Dedicated large-area occlusion sample and stable batching
# --------------------------------------------------------------------------- #
class LargeOcclusionTest(unittest.TestCase):
    def build_frame(self):
        rng = random.Random(20261113)
        instances = []
        n = 0
        # Grid spans a wide field; only the central columns/rows fall inside
        # the wall's projected footprint, leaving visible edge instances.
        # Grid spans nearly the whole field of view; edge columns/rows
        # project outside the central wall and remain visible.
        for gy in range(6):
            for gx in range(8):
                n += 1
                cx = -2.3 + gx * (4.6 / 7)
                cy = -1.85 + gy * (3.7 / 5)
                jitter = rng.uniform(-0.005, 0.005)
                half = 0.03
                instances.append(inst(
                    n, "mat%d" % (n % 4),
                    [cx - half, cy - half, 3.5 + jitter,
                     cx + half, cy + half, 3.7 + jitter]))
        # Central wall; at the instances' depth it covers only the middle
        # columns/rows, so a stable subset is fully occluded and the ring of
        # outer instances stays visible.
        frame = {"occluders": [occluder([10, 6, 22, 18], 1.5)],
                 "instances": instances}
        return frame

    def test_steady_state_culls_at_least_25_percent(self):
        frame = self.build_frame()
        result = cull_render(frame, workers=4)
        culled, visible = assert_matches_ground_truth(
            self, frame, result, expect_cull=True)
        total = len(frame["instances"])
        ratio = len(culled) / total
        self.assertGreaterEqual(ratio, 0.25,
                                "cull ratio %.2f below 25%%" % ratio)

        # Batch membership/order must be invariant under traversal order,
        # worker count, and input permutation.
        reference = cull_render(frame, workers=1, traversal="forward")
        variants = [
            cull_render(frame, workers=1, traversal="reverse"),
            cull_render(frame, workers=4, traversal="forward"),
            cull_render(frame, workers=8, traversal="reverse")]
        shuffled = copy.deepcopy(frame)
        random.Random(7).shuffle(shuffled["instances"])
        variants.append(cull_render(shuffled, workers=5,
                                    traversal="reverse"))
        for other in variants:
            self.assertEqual(other["kept"], reference["kept"])
            self.assertEqual(other["batches"], reference["batches"])
            self.assertEqual(other["ids"], reference["ids"])
            self.assertEqual(
                other["stats"]["hierarchy_queries"],
                reference["stats"]["hierarchy_queries"])

        # Batches carry material identity plus sorted entity ids.
        for batch in result["batches"]:
            self.assertEqual(batch["ids"], sorted(batch["ids"]))
        self.assertEqual([b["material"] for b in result["batches"]],
                         sorted(b["material"] for b in result["batches"]))


# --------------------------------------------------------------------------- #
# Randomised exhaustive conservatism check
# --------------------------------------------------------------------------- #
class FuzzTest(unittest.TestCase):
    def test_random_scenes_never_false_cull(self):
        rng = random.Random(42)
        naive_ever_wrong = False
        for trial in range(60):
            occluders = []
            for _ in range(rng.randint(1, 5)):
                x0 = rng.randint(0, W - 2)
                y0 = rng.randint(0, H - 2)
                x1 = min(W, x0 + rng.randint(1, 12))
                y1 = min(H, y0 + rng.randint(1, 10))
                opaque = rng.random() < 0.85
                occluders.append(
                    occluder([x0, y0, x1, y1],
                             rng.uniform(0.5, 4.0), opaque))
            instances = []
            for iid in range(1, rng.randint(8, 30)):
                cx = rng.uniform(-1.4, 1.4)
                cy = rng.uniform(-1.2, 1.2)
                z0 = rng.uniform(-0.1, 5.0)
                z1 = z0 + rng.uniform(0.05, 1.0)
                hx = rng.uniform(0.01, 0.25)
                hy = rng.uniform(0.01, 0.25)
                instances.append(inst(
                    iid, "m%d" % rng.randint(0, 3),
                    [cx - hx, cy - hy, z0, cx + hx, cy + hy, z1]))
            frame = {"occluders": occluders, "instances": instances}

            result = cull_render(frame, workers=rng.choice([1, 2, 4, 7]))
            assert_matches_ground_truth(self, frame, result)

            ground = full_render(frame)
            visible = {pid for row in ground for pid in row
                       if pid is not None}
            if not visible.issubset(naive_depth_range_cull(frame)):
                naive_ever_wrong = True
        self.assertTrue(naive_ever_wrong)


class OutputContractTest(unittest.TestCase):
    def test_required_fields_and_sorting(self):
        frame = LargeOcclusionTest().build_frame()
        result = cull_render(frame, workers=2)
        self.assertEqual(result["kept"], sorted(result["kept"]))
        self.assertIn("hierarchy_queries", result["stats"])
        self.assertGreater(result["stats"]["hierarchy_queries"], 0)
        for batch in result["batches"]:
            self.assertEqual(set(batch), {"material", "ids"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
