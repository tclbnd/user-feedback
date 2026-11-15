#!/usr/bin/env python3
"""Acceptance tests for atlas.py (stdlib unittest only)."""

import json
import random
import unittest

import atlas


def make_ranked_lights(count=12):
    """`count` lights whose priority ordering is unambiguous (L1 > L2 > ...)."""
    return [
        dict(id="L%d" % i, prio=13 - i, influence=10 - i, dist=i + 2)
        for i in range(1, count + 1)
    ]


def reference_alloc(lights, slots):
    ranked = sorted(
        lights, key=lambda l: (-l["prio"], -l["influence"], l["dist"], str(l["id"]))
    )
    return dict((light["id"], slot) for slot, light in enumerate(ranked[:slots]))


class AllocationTest(unittest.TestCase):
    def test_twelve_lights_eight_slots_match_reference(self):
        lights = make_ranked_lights()
        out = atlas.run(dict(lights=lights, slots=8, frames=[dict(cam_z=5)]))
        self.assertEqual(out["alloc"]["0"], reference_alloc(lights, 8))
        self.assertEqual(out["order"]["0"], ["L%d" % i for i in range(1, 13)])
        self.assertEqual(sorted(out["evict"]), ["L10", "L11", "L12", "L9"])

    def test_low_priority_eviction_keeps_other_mappings(self):
        lights = make_ranked_lights()
        frames = [dict(cam_z=5) for _ in range(10)]
        out = atlas.run(dict(lights=lights, slots=8, frames=frames))
        first = out["alloc"]["0"]
        for frame in out["alloc"].values():
            self.assertEqual(frame, first)

    def test_admitting_a_light_does_not_renumber_others(self):
        top = [dict(id="H%d" % i, prio=100 - i, influence=10, dist=i) for i in range(10)]
        intruder = dict(id="NEW", prio=1, influence=10, dist=1)
        frames = [
            dict(cam_z=1),
            dict(cam_z=1, lights=dict(NEW=dict(prio=200))),
            dict(cam_z=1, lights=dict(NEW=dict(prio=-5))),
        ]
        out = atlas.run(dict(lights=top + [intruder], slots=8, frames=frames))
        f0, f1, f2 = out["alloc"]["0"], out["alloc"]["1"], out["alloc"]["2"]
        for key in [k for k in f0 if k in f1]:
            self.assertEqual(f0[key], f1[key], "slot of %s drifted on admission" % key)
        self.assertIn("NEW", out["evict_frames"]["0"])
        self.assertIn("H7", out["evict_frames"]["1"])
        self.assertIn("NEW", out["evict_frames"]["2"])
        self.assertEqual(f2["H7"], 7, "returning light must reclaim its old slot")

    def test_shuffled_light_order_yields_identical_digest(self):
        lights = make_ranked_lights(12)
        frames = [dict(cam_z=10.0 + i) for i in range(6)]
        base = atlas.run(dict(lights=lights, slots=8, frames=frames))
        for seed in range(8):
            permuted = lights[:]
            random.Random(seed).shuffle(permuted)
            shuffled = atlas.run(dict(lights=permuted, slots=8, frames=frames))
            self.assertEqual(base["digest"], shuffled["digest"])
            self.assertEqual(base["alloc"], shuffled["alloc"])
            self.assertEqual(base["evict"], shuffled["evict"])
            self.assertEqual(base["order"], shuffled["order"])

    def test_camera_jitter_does_not_move_unaffected_lights(self):
        lights = make_ranked_lights(12)
        base = atlas.run(dict(lights=lights, slots=8, frames=[dict(cam_z=10.0)]))
        frames = [dict(cam_z=10.0 + (0.001 if i % 2 else -0.001)) for i in range(12)]
        jittered = atlas.run(dict(lights=lights, slots=8, frames=frames))
        for frame in jittered["alloc"].values():
            self.assertEqual(frame, base["alloc"]["0"])

    def test_ties_break_on_id_deterministically(self):
        lights = [dict(id="B", prio=5), dict(id="A", prio=5), dict(id="C", prio=5)]
        out = atlas.run(dict(lights=lights, slots=2, frames=[dict(cam_z=0)]))
        self.assertEqual(out["alloc"]["0"], dict(A=0, B=1))
        self.assertEqual(out["evict"], ["C"])

    def test_zero_slots_evicts_everything(self):
        out = atlas.run(
            dict(lights=make_ranked_lights(3), slots=0, frames=[dict(cam_z=0)])
        )
        self.assertEqual(out["alloc"]["0"], dict())
        self.assertEqual(out["evict"], ["L1", "L2", "L3"])


class CascadeTest(unittest.TestCase):
    CONFIG = dict(near=0, far=100, count=4, hysteresis=2)

    def run_z(self, zs, config=None):
        lights = [dict(id="sun", type="dir", prio=10, influence=10, dist=1)]
        frames = [dict(cam_z=z) for z in zs]
        return atlas.run(
            dict(lights=lights, slots=8, frames=frames, cascade=config or self.CONFIG)
        )

    def indices(self, out, count):
        return [out["cascade"][str(i)]["sun"] for i in range(count)]

    def test_bounds_and_overlap_bands(self):
        out = self.run_z([0.0])
        self.assertEqual(out["cascade_config"]["bounds"], [0, 25, 50, 75, 100])
        self.assertEqual(out["cascade_config"]["hysteresis"], 2)
        self.assertEqual(
            out["cascade_config"]["overlaps"],
            [[23, 27, 0, 1], [48, 52, 1, 2], [73, 77, 2, 3]],
        )

    def test_oscillation_at_split_switches_once(self):
        zs = [10.0] + [50.0 + (0.4 if i % 2 == 0 else -0.4) for i in range(20)]
        out = self.run_z(zs)
        idx = self.indices(out, len(zs))
        self.assertEqual(idx[0], 0)
        self.assertEqual(idx[-1], 1)
        self.assertEqual(out["cascade_switch_count"].get("sun", 0), 1)
        self.assertEqual(len(out["cascade_switches"]), 1)

    def test_without_hysteresis_the_same_oscillation_flaps(self):
        cfg = dict(self.CONFIG, hysteresis=0)
        zs = [50.0 + (0.4 if i % 2 == 0 else -0.4) for i in range(10)]
        out = self.run_z(zs, cfg)
        self.assertGreaterEqual(out["cascade_switch_count"].get("sun", 0), 9)

    def test_monotone_crossing_switches_once_per_boundary(self):
        zs = [10, 20, 24, 30, 40, 60, 74, 80]
        out = self.run_z(zs)
        self.assertEqual(self.indices(out, len(zs)), [0, 0, 0, 1, 1, 2, 2, 3])
        self.assertEqual(out["cascade_switch_count"]["sun"], 3)
        self.assertEqual([s["frame"] for s in out["cascade_switches"]], [3, 5, 7])

    def test_exact_split_belongs_to_lower_cascade(self):
        for z, expected in ((25.0, 0), (50.0, 1), (75.0, 2), (0.0, 0), (100.0, 3)):
            self.assertEqual(self.run_z([z])["cascade"]["0"]["sun"], expected, z)

    def test_jitter_inside_a_cascade_never_switches(self):
        cfg = dict(self.CONFIG, hysteresis=0)
        zs = [48.5 + (1.0 if i % 2 else -1.0) for i in range(12)]
        out = self.run_z(zs, cfg)
        self.assertEqual(set(self.indices(out, len(zs))), set([1]))

    def test_permuted_lights_keep_cascade_digest(self):
        lights = [
            dict(id="sunA", type="dir", prio=10, influence=10, dist=1),
            dict(id="sunB", type="dir", prio=9, influence=9, dist=2),
        ]
        frames = [dict(cam_z=z) for z in (10, 40, 60, 90)]
        base = atlas.run(
            dict(lights=lights, slots=8, frames=frames, cascade=self.CONFIG)
        )
        shuffled = atlas.run(
            dict(lights=lights[::-1], slots=8, frames=frames, cascade=self.CONFIG)
        )
        self.assertEqual(base["cascade_digest"], shuffled["cascade_digest"])
        self.assertEqual(base["cascade"], shuffled["cascade"])


class PackingTest(unittest.TestCase):
    def test_regions_reported_and_indivisible_sizes_rejected(self):
        lights = [
            dict(id="A", prio=9, tile=[64, 64]),
            dict(id="B", prio=8, tile="96x64"),
            dict(id="C", prio=7, tile=[64, 128]),
            dict(id="D", prio=6, tile=[130, 32]),
            dict(id="E", prio=5, tile=[0, 64]),
        ]
        req = dict(
            lights=lights,
            slots=8,
            frames=[dict(cam_z=0)],
            atlas=dict(width=256, height=192, granularity=32, gap=0, exact_sizes=True),
        )
        out = atlas.run(req)
        self.assertEqual(out["regions"]["0"]["A"], dict(x=0, y=0, w=64, h=64))
        self.assertEqual(out["regions"]["0"]["B"], dict(x=64, y=0, w=96, h=64))
        reasons = dict((f["light"], f["reason"]) for f in out["failures"])
        self.assertEqual(reasons["D"], "indivisible_size")
        self.assertEqual(reasons["E"], "invalid_tile")
        self.assertEqual(set(out["alloc"]["0"]), set(list("ABCDE")))

    def test_oversize_and_atlas_exhaustion(self):
        lights = [dict(id="big", prio=9, tile=[400, 32])]
        lights += [dict(id="s%d" % i, prio=8 - i, tile=[128, 160]) for i in range(3)]
        req = dict(
            lights=lights,
            slots=8,
            frames=[dict(cam_z=0)],
            atlas=dict(width=256, height=256, granularity=32),
        )
        out = atlas.run(req)
        reasons = set((f["light"], f["reason"]) for f in out["failures"])
        self.assertIn(("big", "oversize"), reasons)
        self.assertIn(("s2", "pack_failed"), reasons)
        self.assertEqual(set(out["regions"]["0"]), set(["s0", "s1"]))

    def test_flexible_tiles_are_grown_to_granularity(self):
        lights = [dict(id="A", prio=9, tile=[100, 60])]
        req = dict(
            lights=lights,
            slots=4,
            frames=[dict(cam_z=0)],
            atlas=dict(width=256, height=256, granularity=32),
        )
        out = atlas.run(req)
        region = out["regions"]["0"]["A"]
        self.assertEqual((region["w"], region["h"]), (128, 64))
        self.assertEqual(region["requested"], [100, 60])


class SchemaTest(unittest.TestCase):
    def test_documented_fields_present(self):
        out = atlas.run(
            dict(lights=make_ranked_lights(4), slots=8, frames=[dict(cam_z=1)])
        )
        for key in ("alloc", "evict", "cascade", "digest"):
            self.assertIn(key, out)
        self.assertEqual(out["cascade"]["0"], dict(L1=0, L2=0, L3=0, L4=0))

    def test_output_is_json_serialisable(self):
        req = dict(
            lights=make_ranked_lights(12),
            slots=8,
            frames=[dict(cam_z=1), dict(cam_z=60)],
        )
        json.dumps(atlas.run(req), sort_keys=True)

    def test_empty_and_malformed_input_does_not_raise(self):
        cases = [
            dict(),
            dict(lights=None, slots="x", frames=None),
            dict(
                lights=[dict(id=None), dict(prio=float("nan"))],
                slots=2,
                frames=[dict()],
            ),
        ]
        for req in cases:
            self.assertIn("digest", atlas.run(req))


class AnonymousLightTest(unittest.TestCase):
    """Lights without an id must still be order-independent."""
    def test_anonymous_identity_is_content_derived(self):
        lights = [dict(prio=9, dist=1), dict(prio=1, dist=9), dict(prio=5, dist=5)]
        base = atlas.run(dict(lights=lights, slots=2, frames=[dict(cam_z=0)]))
        shuffled = atlas.run(dict(lights=lights[::-1], slots=2, frames=[dict(cam_z=0)]))
        self.assertEqual(base["digest"], shuffled["digest"])
        self.assertEqual(base["alloc"], shuffled["alloc"])
        self.assertEqual(base["order"], shuffled["order"])
        self.assertEqual(len(base["alloc"]["0"]), 2)
    def test_identical_anonymous_lights_are_reported(self):
        twin = dict(prio=5, influence=1, dist=2)
        out = atlas.run(dict(lights=[dict(twin), dict(twin)], slots=4,
                             frames=[dict(cam_z=0)]))
        self.assertEqual(len(out["alloc"]["0"]), 1)
        self.assertEqual([f["reason"] for f in out["failures"]],
                         ["duplicate_anonymous_light"])
    def test_anonymous_light_keeps_identity_across_frame_overrides(self):
        lights = [dict(prio=9, dist=1), dict(prio=8, dist=2), dict(prio=7, dist=3)]
        probe = atlas.run(dict(lights=lights, slots=2, frames=[dict(cam_z=0)]))
        top = sorted(probe["alloc"]["0"])[0]
        frames = [
            dict(cam_z=0),
            dict(cam_z=0, lights={top: dict(prio=0)}),
            dict(cam_z=0),
        ]
        out = atlas.run(dict(lights=lights, slots=2, frames=frames))
        self.assertIn(top, out["evict_frames"]["1"])
        self.assertIn(top, out["alloc"]["0"])
        # Same slot before and after the excursion.
        self.assertEqual(out["alloc"]["0"][top], out["alloc"]["2"][top])
class DeterminismTest(unittest.TestCase):
    def test_repeated_runs_are_bit_identical(self):
        req = dict(lights=make_ranked_lights(12), slots=8,
                   frames=[dict(cam_z=z) for z in (5, 30, 55, 80)])
        first = json.dumps(atlas.run(req), sort_keys=True)
        second = json.dumps(atlas.run(req), sort_keys=True)
        self.assertEqual(first, second)
    def test_slot_table_does_not_grow_without_bound(self):
        lights = [dict(id="L%03d" % i, prio=1000 - i) for i in range(120)]
        frames = []
        for i in range(120):
            frames.append(dict(cam_z=1, lights=dict([("L%03d" % i, dict(prio=5000 - i))])))
        out = atlas.run(dict(lights=lights, slots=8, frames=frames))
        for frame in out["alloc"].values():
            self.assertEqual(len(frame), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
