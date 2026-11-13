"""Acceptance tests: culling must reproduce the uncropped per-pixel truth."""
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scenes  # noqa: E402
import reference  # noqa: E402
from cull import draw  # noqa: E402


def _ref_ids(image):
    return set(v for row in image for v in row if v is not None)


def assert_matches_independent_reference(scene, workers=1, label="scene"):
    """Compare kept/pixels with a brute-force reference written independently."""
    out = draw(scene, False, workers)
    for fi, frame in enumerate(out["frames"]):
        ref = reference.reference_image(scene, fi)
        assert frame["ids"] == ref, "%s f%d: differs from independent reference" % (
            label, fi)
        ref_visible = reference.visible_ids(ref)
        dropped = set(i["id"] for i in scene["frames"][fi]["instances"]) - set(frame["kept"])
        assert not (dropped & ref_visible), "%s f%d: dropped visible %s" % (
            label, fi, sorted(dropped & ref_visible))


def assert_cull_matches_truth(scene, workers=1, label="scene"):
    """ids must equal the no-cull baseline; every culled id must be invisible."""
    out = draw(scene, False, workers)
    base = draw(scene, True, workers)
    assert len(out["frames"]) == len(base["frames"])
    for fi, (fr, bfr) in enumerate(zip(out["frames"], base["frames"])):
        assert fr["ids"] == bfr["ids"], "%s f%d: ids differ from baseline" % (label, fi)
        truth_ids = _ref_ids(bfr["ids"])
        culled = set(i["id"] for i in scene["frames"][fi]["instances"]) - set(fr["kept"])
        visible_culled = culled & truth_ids
        assert not visible_culled, "%s f%d: culled but visible: %s" % (
            label, fi, sorted(visible_culled))
    return out


def _run(scene, workers=1):
    return draw(scene, False, workers)


def test_mass_occlusion():
    scene = scenes.mass_occlusion_scene()
    out = _run(scene, workers=4)
    n = len(scene["frames"][0]["instances"])
    for fi in range(3):  # steady state, three identical frames
        frame = out["frames"][fi]
        assert len(frame["kept"]) <= 0.75 * n, "must cull >=25%%, kept %d/%d" % (
            len(frame["kept"]), n)
        assert frame["stats"]["hierarchy_queries"] > 0
    # The wall is gone in the last frame, so every instance must come back.
    assert out["frames"][3]["kept"] == sorted(i["id"] for i in scene["frames"][3]["instances"])
    assert_cull_matches_truth(scene, workers=4, label="mass")


def test_thin_occluder():
    scene = scenes.thin_occluder_scene()
    out = assert_cull_matches_truth(scene, label="thin")
    # A one-pixel sliver through the middle cannot hide the whole instance.
    for frame in out["frames"]:
        assert 1 in frame["kept"]


def test_near_crossing():
    scene = scenes.near_crossing_scene()
    out = assert_cull_matches_truth(scene, label="near")
    # Crossing boxes are conservatively kept, including after the box has
    # passed the near plane and is no longer renderable.
    for ident in (1, 2):
        assert ident in out["frames"][0]["kept"]


def test_fast_motion():
    scene = scenes.fast_motion_scene()
    assert_cull_matches_truth(scene, label="fast", workers=3)
    # Occluder covers the left half for every frame, so some culling happens.
    assert any(len(f["kept"]) < 16 for f in _run(scene)["frames"])


def test_transparent_occluder():
    scene = scenes.transparent_occluder_scene()
    out = assert_cull_matches_truth(scene, label="transparent")
    assert out["frames"][0]["kept"] == []
    assert out["frames"][1]["kept"] == sorted(
        i["id"] for i in scene["frames"][1]["instances"])
    assert out["frames"][2]["kept"] == []


def test_offscreen_and_behind_near():
    scene = scenes.offscreen_scene()
    out = assert_cull_matches_truth(scene, label="offscreen")
    assert out["frames"][0]["kept"] == [3]


def test_randomized_soundness():
    for seed in range(12):
        scene = scenes.randomized_scene(seed)
        assert_cull_matches_truth(scene, workers=1 + seed % 4, label="rnd%d" % seed)


def test_determinism_across_order_and_workers():
    for scene in (scenes.mass_occlusion_scene(), scenes.randomized_scene(9),
                  scenes.near_crossing_scene()):
        reference = draw(scene, False, 1)
        for workers in (2, 4, 8):
            for seed in (0, 7, 42):
                other = draw(scenes.shuffled(scene, seed), False, workers)
                assert other == reference, "order/workers changed output (w=%d s=%d)" % (
                    workers, seed)


def test_temporal_hint_cannot_change_result():
    """Identical consecutive frames may reuse derived state; output stays exact."""
    scene = scenes.randomized_scene(11)
    repeated = dict(size=list(scene["size"]), near=scene["near"], frames=[])
    for frame in scene["frames"]:
        repeated["frames"].append(dict(occluders=list(frame["occluders"]),
                                       instances=list(frame["instances"])))
        repeated["frames"].append(dict(occluders=list(frame["occluders"]),
                                       instances=list(frame["instances"])))
    out = assert_cull_matches_truth(repeated, label="repeat")
    # Paired frames must agree, both in visibility and in the per-pixel result.
    for i in range(0, len(out["frames"]), 2):
        assert out["frames"][i]["kept"] == out["frames"][i + 1]["kept"]
        assert out["frames"][i]["ids"] == out["frames"][i + 1]["ids"]
    assert out == draw(repeated, False, 1)


def test_hint_falls_back_when_frame_changes():
    """A changed frame must not inherit the previous visible set."""
    scene = scenes.fast_motion_scene()
    out = draw(scene, False, 1)
    # Frame 1 hides a different set of instances than frame 0; both must equal
    # what a single-frame evaluation produces with no history at all.
    frames = scene["frames"]
    for fi in range(1, len(frames)):
        isolated = dict(size=list(scene["size"]), near=scene["near"],
                        frames=[frames[fi]])
        alone = draw(isolated, False, 1)["frames"][0]
        assert alone["kept"] == out["frames"][fi]["kept"]
        assert alone["ids"] == out["frames"][fi]["ids"]


def test_degenerate_geometry():
    """Empty, inverted and off-screen primitives must not crash or lie."""
    width, height, near = 16, 12, 0.25
    instances = [
        dict(id=1, material="a", box=[0.0, 0.0, 1.0, 0.0, 0.0, 1.0]),   # zero area
        dict(id=2, material="a", box=[0.2, 0.2, 1.0, -0.2, -0.2, 1.1]),  # inverted
        dict(id=3, material="b", box=[-0.2, -0.2, 2.5, 0.2, 0.2, 2.6]),
    ]
    occluders = [
        dict(rect=[4, 4, 4, 8], z=1.0, opaque=True),    # empty rect
        dict(rect=[8, 8, 2, 2], z=1.0, opaque=True),    # inverted rect
        dict(rect=[-20, -20, -10, -10], z=1.0, opaque=True),  # off screen
    ]
    scene = dict(size=[width, height], near=near,
                 frames=[dict(occluders=occluders, instances=instances)])
    out = assert_cull_matches_truth(scene, label="degenerate")
    # A zero-area projection covers no pixel centre, so dropping it is sound.
    assert out["frames"][0]["kept"] == [2, 3]


def test_depth_tie_is_retained():
    """Equal depth must keep the instance, never prove occlusion."""
    width, height, near = 16, 12, 0.25
    instances = [
        dict(id=1, material="a", box=[-0.2, -0.2, 2.0, 0.2, 0.2, 2.1]),
        dict(id=2, material="a", box=[-0.2, -0.2, 2.0000001, 0.2, 0.2, 2.1]),
    ]
    frame = dict(occluders=[dict(rect=[0, 0, width, height], z=2.0, opaque=True)],
                 instances=instances)
    out = assert_cull_matches_truth(
        dict(size=[width, height], near=near, frames=[frame]), label="tie")
    assert 1 in out["frames"][0]["kept"]
    assert 2 not in out["frames"][0]["kept"]


def test_batches_are_sorted_and_stable():
    scene = scenes.randomized_scene(3)
    out = draw(scene, False, 2)
    for fi, frame in enumerate(out["frames"]):
        materials = [b["material"] for b in frame["batches"]]
        assert materials == sorted(materials)
        for batch in frame["batches"]:
            assert batch["ids"] == sorted(batch["ids"])
        flat = list(itertools.chain.from_iterable(b["ids"] for b in frame["batches"]))
        assert sorted(flat) == frame["kept"]
        # Material identity must come from the instance definition.
        want = {}
        for i in scene["frames"][fi]["instances"]:
            want.setdefault(i["material"], set()).add(i["id"])
        for batch in frame["batches"]:
            assert set(batch["ids"]) <= want[batch["material"]]


def test_matches_independent_reference():
    """All scenarios cross-checked against the brute-force reference."""
    scenarios = [
        ("mass", scenes.mass_occlusion_scene()),
        ("thin", scenes.thin_occluder_scene()),
        ("near", scenes.near_crossing_scene()),
        ("fast", scenes.fast_motion_scene()),
        ("offscreen", scenes.offscreen_scene()),
        ("transparent", scenes.transparent_occluder_scene()),
    ]
    for seed in range(6):
        scenarios.append(("rnd%d" % seed, scenes.randomized_scene(seed)))
    for label, scene in scenarios:
        assert_matches_independent_reference(scene, label=label)


def test_no_cull_baseline_matches_renderer():
    for scene in (scenes.mass_occlusion_scene(), scenes.randomized_scene(5)):
        out = draw(scene, True, 1)
        for fi, frame in enumerate(out["frames"]):
            assert frame["kept"] == sorted(
                i["id"] for i in scene["frames"][fi]["instances"])
            assert frame["stats"]["hierarchy_queries"] == 0
