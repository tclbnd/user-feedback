"""Acceptance checks for the offline temporal reconstructor.

Run with ``python3 verify.py``.  Everything uses the standard library only and
the geometry/statistics below are implemented independently of taa.py.
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import taa


# ---------------------------------------------------------------------------
# Independent small helpers (written separately from the renderer).
# ---------------------------------------------------------------------------
def aff(m, u, v):
    return (m[0] * u + m[1] * v + m[4], m[2] * u + m[3] * v + m[5])


def aff_inv(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    return [d / det, -b / det, -c / det, a / det,
            (b * ty - d * tx) / det, (c * tx - a * ty) / det]


def bg(x, y):
    return 0.32 + 0.26 * (math.floor(1.75 * x + 0.25 * y) % 2)


def rotation(theta_deg, tx, ty):
    t = math.radians(theta_deg)
    return [math.cos(t), -math.sin(t), math.sin(t), math.cos(t), tx, ty]


def mean(values):
    values = list(values)
    return sum(values) / len(values)


def population_variance(values):
    m = mean(values)
    return mean([(v - m) ** 2 for v in values])


FAILURES = []


def check(condition, label):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# Legacy scene: 64-sample reference, variance and linear-colour error.
# ---------------------------------------------------------------------------
def legacy_sample(frame, x, y, oxs, oys):
    """Render one sub-pixel sample independently of taa.render()."""
    cam_x, cam_y = frame["camera"]
    obj_x, obj_y = frame["object"]
    jx, jy = frame["jitter"]
    wx = x + oxs + cam_x + jx
    wy = y + oys + cam_y + jy
    if obj_x <= wx < obj_x + 5 and obj_y <= wy < obj_y + 4:
        return 1, 0.24, 0.87
    return 0, 0.80, bg(wx, wy)


def reference_64(frame):
    """Deterministic 8x8 sub-pixel reference, mean of linear luminance."""
    w, h = frame["size"]
    offsets = [((i + 0.5) / 8.0, (j + 0.5) / 8.0)
               for i in range(8) for j in range(8)]
    out = [[0.0] * w for _ in range(h)]
    ids = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            vals = [legacy_sample(frame, x, y, sx, sy) for sx, sy in offsets]
            out[y][x] = mean([v[2] for v in vals])
            ids[y][x] = 1 if all(v[0] == 1 for v in vals) else 0
    return out, ids


def build_legacy_sequence():
    # Static camera and object; the only motion is sub-pixel jitter, so the
    # high-frequency stripe background is the temporal-noise stress case.
    jitters = [(0.00, 0.00), (0.25, 0.00), (0.50, 0.25), (0.25, 0.50),
               (0.00, 0.25), (-0.25, 0.00), (-0.5, -0.25), (-0.25, -0.5),
               (0.00, -0.25), (0.25, 0.25), (0.50, 0.00), (0.25, -0.25)]
    return [{"size": [24, 18], "camera": [0.0, 0.0],
             "object": [4.0, 3.0], "jitter": [jx, jy]} for jx, jy in jitters]


def test_legacy_quality():
    print("\n== legacy 64-sample reference, variance and colour error ==")
    frames = build_legacy_sequence()
    engine = taa.Temporal()
    taa_out = [engine.step(f, True) for f in frames]
    # The raw baseline is the original 1-sample renderer contract; this is the
    # noisy signal temporal accumulation is expected to suppress.
    raw_out = [taa.render(f) for f in frames]
    refs = [reference_64(f) for f in frames]

    warm = 2
    h, w = frames[0]["size"][1], frames[0]["size"][0]
    bg_pixels = [(x, y) for y in range(h) for x in range(w)
                 if refs[0][1][y][x] == 0]

    var_raw, var_taa, mae_taa, mae_raw = [], [], [], []
    for x, y in bg_pixels:
        series_raw = [raw_out[t][y][x]["color"]
                      for t in range(warm, len(frames))]
        series_taa = [taa_out[t]["color"][y][x]
                      for t in range(warm, len(frames))]
        var_raw.append(population_variance(series_raw))
        var_taa.append(population_variance(series_taa))
        for t in range(warm, len(frames)):
            ref = refs[t][0][y][x]
            mae_taa.append(abs(taa_out[t]["color"][y][x] - ref))
            mae_raw.append(abs(raw_out[t][y][x]["color"] - ref))

    reduction = 1.0 - mean(var_taa) / mean(var_raw)
    print(f"raw temporal variance mean: {mean(var_raw):.6f}")
    print(f"taa temporal variance mean: {mean(var_taa):.6f}")
    print(f"variance reduction: {reduction * 100:.1f}%")
    print(f"linear colour MAE (taa vs 64-sample): {mean(mae_taa):.4f} "
          f"max {max(mae_taa):.4f}")
    print(f"linear colour MAE (raw vs 64-sample): {mean(mae_raw):.4f}")
    check(reduction >= 0.25, f"variance reduction >= 25% (got {reduction:.3f})")
    check(mean(mae_taa) <= 0.05,
          f"linear colour error <= 0.05 (got {mean(mae_taa):.4f})")
    check(mean(mae_taa) <= mean(mae_raw), "TAA no worse than raw 1-sample")


def test_legacy_moving_occlusion():
    print("\n== legacy moving occlusion: no cross-layer bleed ==")
    frames = [
        {"size": [16, 12], "camera": [0, 0], "object": [6, 4],
         "jitter": [0, 0]},
        # Quad jumps right: its old footprint reveals background pixels.
        {"size": [16, 12], "camera": [0, 0], "object": [9, 4],
         "jitter": [0, 0]},
    ]
    engine = taa.Temporal()
    out = [engine.step(f, True) for f in frames]
    raw0 = taa.render(frames[0])
    raw1 = taa.render(frames[1])
    leaks = 0
    disoccluded = 0
    for y in range(12):
        for x in range(16):
            if raw0[y][x]["id"] == 1 and raw1[y][x]["id"] == 0:
                disoccluded += 1
                # A revealed pixel may contain mixed sub-pixel coverage, but
                # every foreground-id sub-sample must be rejected: no history
                # weight and no foreground colour may bleed into the pixel.
                sub = taa.render_legacy_subsamples(frames[1])[y][x]
                fg_subs = [k for k, s in enumerate(sub) if s["id"] == 1]
                if any(out[1]["sample_weights"][y][x][k] != 0.0
                       for k in fg_subs):
                    leaks += 1
                if any(out[1]["sample_reasons"][y][x][k] != "id_mismatch"
                       for k in fg_subs):
                    leaks += 1
                current_mean = mean(s["color"] for s in sub)
                if abs(out[1]["color"][y][x] - current_mean) > 1e-12:
                    leaks += 1
    check(disoccluded > 0, f"there are disoccluded pixels ({disoccluded})")
    check(leaks == 0, "disoccluded pixels get zero history and own colour")
    kept = [(x, y) for y in range(12) for x in range(16)
            if raw0[y][x]["id"] == 1 and raw1[y][x]["id"] == 1]
    reused = [out[1]["weights"][y][x] > 0 for x, y in kept]
    check(bool(kept) and all(reused),
          f"persistent foreground keeps history ({len(kept)} pixels)")


def test_legacy_cli_and_replay():
    print("\n== legacy CLI, --no-history and truncated replay ==")
    frames = [
        {"size": [10, 8], "camera": [0, 0], "object": [2, 2],
         "jitter": [0, 0]},
        {"size": [10, 8], "camera": [0.5, 0], "object": [3, 2],
         "jitter": [0.25, 0]},
        {"size": [10, 8], "camera": [0, 0], "object": [1, 2],
         "jitter": [0, 0], "cut": True},
        {"size": [10, 8], "camera": [0.25, 0.25], "object": [1.5, 2],
         "jitter": [0.1, 0.1]},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scene_path = tmp / "scene.json"
        scene_path.write_text(json.dumps({"frames": frames}))
        full_out = tmp / "full.json"
        subprocess.run([sys.executable, "taa.py", str(scene_path),
                        str(full_out)], check=True)
        full = json.loads(full_out.read_text())["frames"]

        no_out = tmp / "no.json"
        subprocess.run([sys.executable, "taa.py", str(scene_path),
                        str(no_out), "--no-history"], check=True)
        no = json.loads(no_out.read_text())["frames"]

        cut_path = tmp / "cutscene.json"
        cut_path.write_text(json.dumps({"frames": frames[2:]}))
        cut_out = tmp / "cut.json"
        subprocess.run([sys.executable, "taa.py", str(cut_path),
                        str(cut_out)], check=True)
        cut = json.loads(cut_out.read_text())["frames"]

    check(all(all(w == 0.0 for w in row)
              for f in no for row in f["weights"]),
          "--no-history weights are all zero")
    check(all(r == "disabled" for f in no for row in f["reasons"]
              for r in row), "--no-history reasons are 'disabled'")
    # The temporal path renders an 8x8 sub-pixel grid; disabled output is the
    # mean of those current-frame linear samples with zero history blend.
    expected = [
        [[mean(s["color"] for s in cell) for cell in row]
         for row in taa.render_legacy_subsamples(f)]
        for f in frames]
    same = all(abs(no[t]["color"][y][x] - expected[t][y][x]) < 1e-12
               for t in range(len(frames)) for y in range(8)
               for x in range(10))
    check(same, "--no-history colour equals current-frame sub-pixel render")

    check(full[2]["reset"] == "cut" and full[0]["reset"] == "first_frame",
          "first frame and cut report resets")
    identical = (json.dumps(full[2:], sort_keys=True)
                 == json.dumps(cut, sort_keys=True))
    check(identical, "replay from the cut matches the full run segment")


# ---------------------------------------------------------------------------
# Multi-layer scene: independent geometry solver and history checks.
# ---------------------------------------------------------------------------
def solve_sample(frame, x, y, k):
    """Independent reference: visible id, colour and depth of one 2x2 sample."""
    sx, sy = taa.SAMPLE_OFFSETS[k]
    jx, jy = frame["jitter"]
    view = frame.get("view", [1, 0, 0, 1, 0, 0])
    su, sv = x + sx + jx, y + sy + jy
    wx, wy = aff(view, su, sv)
    cands = [(0, 0.80, bg(wx, wy))]
    for layer in frame["layers"]:
        lu, lv = aff(aff_inv(layer["transform"]), wx, wy)
        lw, lh = layer["size"]
        if 0 <= lu < lw and 0 <= lv < lh:
            z0, zx, zy = layer["depth"]
            cands.append((layer["id"], z0 + zx * lu + zy * lv,
                          layer["color"]))
    cands.sort(key=lambda c: (c[1], c[0]))
    return cands[0]


def test_layer_geometry_and_coverage():
    print("\n== layer scene: independent solver, 2x2 coverage and resolve ==")
    frame = {
        "size": [8, 6], "jitter": [0, 0],
        "view": [1.2, 0.1, -0.1, 1.1, 0.3, -0.2],
        "layers": [
            {"id": 1, "size": [4, 3], "transform": rotation(20, 1.0, 1.0),
             "depth": [0.3, 0.0, 0.0], "color": 0.7},
            {"id": 2, "size": [3, 4], "transform": rotation(-35, 3.2, 0.4),
             "depth": [0.5, 0.01, 0.0], "color": 0.4},
        ],
    }
    out = taa.LayerTemporal().step(frame, True)
    mismatches = 0
    partial_pixels = 0
    for y in range(6):
        for x in range(8):
            ids_here = set()
            for k in range(4):
                ref_id, ref_z, ref_col = solve_sample(frame, x, y, k)
                if out["sample_ids"][y][x][k] != ref_id:
                    mismatches += 1
                if abs(out["sample_colors"][y][x][k] - ref_col) > 1e-12:
                    mismatches += 1
                ids_here.add(ref_id)
            if len(ids_here) > 1:
                partial_pixels += 1
            expect_color = mean([solve_sample(frame, x, y, k)[2]
                                 for k in range(4)])
            if abs(out["color"][y][x] - expect_color) > 1e-12:
                mismatches += 1
            if abs(out["weights"][y][x]
                   - mean(out["sample_weights"][y][x])) > 1e-12:
                mismatches += 1
    check(mismatches == 0, "sample ids, linear colours and pixel resolve match")
    check(partial_pixels > 0,
          f"pixels with mixed 2x2 coverage exist ({partial_pixels})")
    flat_weights = [w for row in out["sample_weights"] for cell in row
                    for w in cell]
    check(all(w == 0.0 for w in flat_weights),
          "first frame history weights are zero")
    check(out["reset"] == "first_frame", "first frame is a reset")


def build_layer_sequence():
    view = [1.1, 0.05, -0.05, 0.95, 0.4, -0.3]
    base = {
        "size": [10, 8], "jitter": [0, 0], "view": view,
        "layers": [
            {"id": 1, "size": [4, 3], "transform": rotation(0, 2.0, 2.0),
             "depth": [0.30, 0.0, 0.0], "color": 0.70},
            {"id": 2, "size": [3, 3], "transform": rotation(0, 5.0, 1.5),
             "depth": [0.50, 0.0, 0.0], "color": 0.40},
        ],
    }

    def clone(transforms=None, jitter=None, depth=None, cut=False,
              size=None, view_override=None):
        f = json.loads(json.dumps(base))
        if transforms:
            for layer, t in zip(f["layers"], transforms):
                layer["transform"] = t
        if depth:
            for layer, d in zip(f["layers"], depth):
                layer["depth"] = d
        if jitter:
            f["jitter"] = jitter
        if cut:
            f["cut"] = True
        if size:
            f["size"] = size
        if view_override is not None:
            f["view"] = view_override
        return f

    frames = [
        clone(),                                                    # 0 reset
        clone(jitter=[0.5, 0.0]),                                   # 1 half px
        clone(transforms=[rotation(25, 2.2, 2.1),
                          rotation(-20, 4.8, 1.7)],
              jitter=[0.25, -0.25]),                                # 2 rotate
        # Depth swap: layer 2 jumps in front of layer 1, geometry unchanged.
        clone(transforms=[rotation(25, 2.2, 2.1),
                          rotation(-20, 4.8, 1.7)],
              depth=[[0.60, 0.0, 0.0], [0.20, 0.0, 0.0]],
              jitter=[0.0, 0.25]),                                  # 3 swap
        # Same geometry/depths as 3 but a cut: must reset.
        clone(transforms=[rotation(25, 2.2, 2.1),
                          rotation(-20, 4.8, 1.7)],
              depth=[[0.60, 0.0, 0.0], [0.20, 0.0, 0.0]],
              jitter=[0.1, 0.1], cut=True),                         # 4 cut
        # Viewport change after the cut.
        clone(transforms=[rotation(30, 2.0, 2.0),
                          rotation(-10, 5.0, 1.5)],
              size=[12, 9],
              view_override=[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]),        # 5 resize
        # View change without resize: reprojection must still find history.
        clone(transforms=[rotation(30, 2.0, 2.0),
                          rotation(-10, 5.0, 1.5)],
              size=[12, 9],
              view_override=[1.15, 0.02, -0.02, 0.9, 0.2, 0.1]),    # 6 view
    ]
    return frames


def test_layer_temporal():
    print("\n== layer scene: reprojection, depth swap, resets, replay ==")
    frames = build_layer_sequence()
    engine = taa.LayerTemporal()
    out = [engine.step(f, True) for f in frames]
    no_engine = taa.LayerTemporal()
    no_out = [no_engine.step(f, False) for f in frames]

    # Geometry ids always match the independent solver, every frame.
    geom_bad = 0
    for t, f in enumerate(frames):
        h = f["size"][1]
        w = f["size"][0]
        for y in range(h):
            for x in range(w):
                for k in range(4):
                    if out[t]["sample_ids"][y][x][k] != \
                            solve_sample(f, x, y, k)[0]:
                        geom_bad += 1
    check(geom_bad == 0, "all frames match independent visible-id solver")

    # Frame 1: static geometry, half-pixel jitter -> interior samples reuse.
    interior_reuse = [out[1]["sample_weights"][y][x][k]
                      for y in range(1, 7) for x in range(1, 9)
                      for k in range(4)
                      if out[1]["sample_ids"][y][x][k] != 0]
    check(bool(interior_reuse) and all(w > 0 for w in interior_reuse),
          f"static interior samples reuse history ({len(interior_reuse)})")

    # Frame 2: rotating layers.  Interior samples of each layer keep finding
    # same-id history through local-point reprojection; samples rejected at a
    # layer's own motion boundary must be explicit id/out-of-bounds rejections.
    rot_reuse = [out[2]["sample_weights"][y][x][k]
                 for y in range(2, 6) for x in range(2, 8)
                 for k in range(4)
                 if out[2]["sample_ids"][y][x][k] in (1, 2)]
    check(any(w > 0 for w in rot_reuse)
          and sum(1 for w in rot_reuse if w > 0) >= 0.75 * len(rot_reuse),
          f"rotating layers reuse same-object history "
          f"({sum(1 for w in rot_reuse if w > 0)}/{len(rot_reuse)})")
    check(all(out[2]["sample_reasons"][y][x][k] in
              ("accepted", "id_mismatch", "out_of_bounds", "depth_mismatch")
              for y in range(8) for x in range(10) for k in range(4)),
          "every rejected rotating sample has an explicit reason")

    # Frame 3: depth swap.  The hard guarantee is that a reconstructed sample
    # can only mix colours of its own visible object (or stay at its current
    # colour); the other layer's luminance must never appear.
    layer_colors = {layer["id"]: layer["color"]
                    for layer in frames[3]["layers"]}
    swapped_leaks = 0
    for y in range(8):
        for x in range(10):
            for k in range(4):
                cur_id = out[3]["sample_ids"][y][x][k]
                w = out[3]["sample_weights"][y][x][k]
                col = out[3]["sample_colors"][y][x][k]
                raw_col = solve_sample(frames[3], x, y, k)[2]
                if cur_id in layer_colors:
                    own = layer_colors[cur_id]
                    # Convex combination of own constant colour and itself.
                    if abs(col - own) > 1e-12:
                        swapped_leaks += 1
                else:
                    # Background samples keep a background stripe luminance.
                    if not (0.32 - 1e-9 <= col <= 0.58 + 1e-9):
                        swapped_leaks += 1
                if w == 0 and out[3]["sample_reasons"][y][x][k] not in (
                        "id_mismatch", "out_of_bounds", "depth_mismatch"):
                    swapped_leaks += 1
                if abs(col - raw_col) > 1e-9 and w == 0:
                    swapped_leaks += 1
    check(swapped_leaks == 0,
          "depth swap never blends another layer's luminance")
    # On the swap frame every layer sample changes depth plane, so per the
    # 0.025 tolerance they must all reject (no stale-depth accumulation);
    # background samples, whose depth is constant, keep reusing history.
    layer_w = [out[3]["sample_weights"][y][x][k]
               for y in range(8) for x in range(10) for k in range(4)
               if out[3]["sample_ids"][y][x][k] in (1, 2)]
    check(bool(layer_w) and all(w == 0.0 for w in layer_w),
          "depth-swap layer samples reject stale history by depth test")
    bg_w = [out[3]["sample_weights"][y][x][k]
            for y in range(8) for x in range(10) for k in range(4)
            if out[3]["sample_ids"][y][x][k] == 0]
    check(any(w > 0 for w in bg_w),
          "constant-depth background still reuses history across the swap")
    # Same-id, same-depth samples on a non-swap frame do accumulate normally.
    kept = [out[2]["sample_weights"][y][x][k]
            for y in range(8) for x in range(10) for k in range(4)
            if out[2]["sample_ids"][y][x][k] in (1, 2)]
    check(any(w > 0 for w in kept),
          "samples with valid same-id history reuse it on stable frames")

    depth_reasons = {r for row in out[3]["sample_reasons"] for cell in row
                     for r in cell}
    check("depth_mismatch" in depth_reasons or "id_mismatch" in depth_reasons,
          "footprint validation records id/depth rejection reasons")

    # Cuts and viewport changes reset.
    check(out[4]["reset"] == "cut", "cut frame resets history")
    flat4 = [w for row in out[4]["sample_weights"] for cell in row
             for w in cell]
    check(all(w == 0.0 for w in flat4), "cut frame weights are zero")
    check(out[5]["reset"] == "size_change", "viewport change resets history")
    flat5 = [w for row in out[5]["sample_weights"] for cell in row
             for w in cell]
    check(all(w == 0.0 for w in flat5),
          "viewport-change frame weights are zero")
    check(out[6]["reset"] is None, "view-only change does not reset")

    # Frame 6 vs frame 5: view change but same size; interior samples reuse.
    view_reuse = [out[6]["sample_weights"][y][x][k]
                  for y in range(1, 8) for x in range(1, 11)
                  for k in range(4)]
    check(any(w > 0 for w in view_reuse),
          "affine view change still yields history reuse")

    # --no-history: zero weights, current-frame linear colours only.
    disabled_ok = all(
        w == 0.0
        for f in no_out for row in f["sample_weights"] for cell in row
        for w in cell)
    colour_ok = all(
        abs(no_out[t]["sample_colors"][y][x][k]
            - solve_sample(frames[t], x, y, k)[2]) < 1e-12
        for t in range(len(frames))
        for y in range(frames[t]["size"][1])
        for x in range(frames[t]["size"][0]) for k in range(4))
    check(disabled_ok, "--no-history layer weights are all zero")
    check(colour_ok, "--no-history layer colours are current-frame only")
    check(all(f["reset"] == "disabled" for f in no_out),
          "--no-history reports the disabled reason")

    # Replay from the cut must be identical to the full run tail.
    engine2 = taa.LayerTemporal()
    replay = [engine2.step(f, True) for f in frames[4:]]
    same = json.dumps(replay, sort_keys=True) == \
        json.dumps(out[4:], sort_keys=True)
    check(same, "replay starting at the cut reproduces the full run")


def test_layer_all_invalid_footprint():
    print("\n== layer scene: fully invalid footprint forces zero weight ==")
    frames = [
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [3, 3],
                     "transform": [1, 0, 0, 1, 2.0, 2.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        # The layer jumps far outside the viewport: every old layer sample is
        # out of bounds for current samples and no same-id tap can survive.
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [3, 3],
                     "transform": [1, 0, 0, 1, 40.0, 40.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        # It comes back: history exists again from frame 1's baseline.
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [3, 3],
                     "transform": [1, 0, 0, 1, 2.0, 2.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
    ]
    engine = taa.LayerTemporal()
    out = [engine.step(f, True) for f in frames]
    # While the layer is outside the viewport every layer-id sample must fail;
    # background samples may still reuse stationary background history.
    layer_weights = [out[1]["sample_weights"][y][x][k]
                     for y in range(6) for x in range(8) for k in range(4)
                     if out[1]["sample_ids"][y][x][k] == 1]
    check(layer_weights == [], "no layer-id samples exist while it is away")
    layer_present = [out[1]["sample_ids"][y][x][k]
                     for y in range(6) for x in range(8) for k in range(4)]
    check(1 not in layer_present, "the moved layer leaves the viewport")
    check(any(r in ("out_of_bounds", "id_mismatch")
              for row in out[1]["sample_reasons"] for cell in row
              for r in cell), "rejected samples carry an explicit reason")
    # The dedicated all-invalid construction: query footprints whose taps all
    # fail the id test must individually carry zero history weight.
    hard_frames = [
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [3, 3],
                     "transform": [1, 0, 0, 1, 2.0, 2.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 2, "size": [3, 3],
                     "transform": [1, 0, 0, 1, 2.0, 2.0],
                     "depth": [0.3, 0, 0], "color": 0.4}]},
    ]
    hard_engine = taa.LayerTemporal()
    hard_out = [hard_engine.step(f, True) for f in hard_frames]
    hard_weights = [hard_out[1]["sample_weights"][y][x][k]
                    for y in range(6) for x in range(8) for k in range(4)
                    if hard_out[1]["sample_ids"][y][x][k] == 2]
    check(bool(hard_weights) and all(w == 0.0 for w in hard_weights),
          "all-invalid (id-changed) footprint forces zero history weight")
    check(all(hard_out[1]["sample_reasons"][y][x][k] == "id_mismatch"
              for y in range(6) for x in range(8) for k in range(4)
              if hard_out[1]["sample_ids"][y][x][k] == 2),
          "all-invalid samples report id_mismatch")
    # A layer that stays in view but changes depth plane stores a baseline its
    # return can reuse; verify baseline accumulation across three frames.
    depth_frames = [
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [4, 4],
                     "transform": [1, 0, 0, 1, 1.5, 1.5],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        {"size": [8, 6], "jitter": [0.2, 0.1],
         "layers": [{"id": 1, "size": [4, 4],
                     "transform": [1, 0, 0, 1, 1.5, 1.5],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        {"size": [8, 6], "jitter": [0.0, 0.0],
         "layers": [{"id": 1, "size": [4, 4],
                     "transform": [1, 0, 0, 1, 1.5, 1.5],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
    ]
    depth_engine = taa.LayerTemporal()
    depth_out = [depth_engine.step(f, True) for f in depth_frames]
    reused = [depth_out[2]["sample_weights"][y][x][k]
              for y in range(6) for x in range(8) for k in range(4)
              if depth_out[2]["sample_ids"][y][x][k] == 1]
    check(bool(reused) and all(w > 0 for w in reused),
          "a stationary layer keeps reusing its stored baseline")


def test_layer_bilinear_normalization():
    print("\n== layer scene: bilinear footprint renormalisation ==")
    # Static geometry with jitter so footprints straddle four history nodes;
    # accepted weights must be positive and bounded by the blend alpha.
    frames = [
        {"size": [8, 6], "jitter": [0, 0],
         "layers": [{"id": 1, "size": [6, 4],
                     "transform": [1, 0, 0, 1, 1.0, 1.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
        {"size": [8, 6], "jitter": [0.3, 0.2],
         "layers": [{"id": 1, "size": [6, 4],
                     "transform": [1, 0, 0, 1, 1.0, 1.0],
                     "depth": [0.3, 0, 0], "color": 0.7}]},
    ]
    engine = taa.LayerTemporal()
    out = [engine.step(f, True) for f in frames]
    interior = [out[1]["sample_weights"][y][x][k]
                for y in range(1, 5) for x in range(1, 7) for k in range(4)]
    check(all(0.0 < w <= taa.HISTORY_ALPHA + 1e-12 for w in interior),
          "renormalised interior weights are positive and bounded")
    const_ok = all(abs(out[1]["sample_colors"][y][x][k] - 0.7) < 1e-12
                   for y in range(6) for x in range(8) for k in range(4)
                   if out[1]["sample_ids"][y][x][k] == 1)
    check(const_ok, "constant-colour layer reconstructs without bleeding")


def test_layer_depth_rules():
    print("\n== layer scene: depth plane rejection and equal-depth order ==")
    layer = dict(id=1, size=[5, 4], transform=[1, 0, 0, 1, 1.0, 1.0],
                 depth=[0.30, 0.0, 0.0], color=0.7)
    f0 = {"size": [8, 6], "jitter": [0, 0], "layers": [dict(layer)]}

    # Constant offset larger than the 0.025 tolerance must reject the taps.
    f_big = {"size": [8, 6], "jitter": [0, 0],
             "layers": [dict(layer, depth=[0.40, 0.0, 0.0])]}
    engine_big = taa.LayerTemporal()
    out_big = [engine_big.step(f, True) for f in (f0, f_big)]
    reasons_big = {r for row in out_big[1]["sample_reasons"] for cell in row
                   for r in cell}
    check("depth_mismatch" in reasons_big,
          "depth plane move beyond tolerance yields depth_mismatch")
    big_layer_w = [out_big[1]["sample_weights"][y][x][k]
                   for y in range(6) for x in range(8) for k in range(4)
                   if out_big[1]["sample_ids"][y][x][k] == 1]
    check(bool(big_layer_w) and all(w == 0.0 for w in big_layer_w),
          "every layer sample rejects history when its plane moves")
    big_bg_w = [out_big[1]["sample_weights"][y][x][k]
                for y in range(6) for x in range(8) for k in range(4)
                if out_big[1]["sample_ids"][y][x][k] == 0]
    check(any(w > 0 for w in big_bg_w),
          "background history is unaffected by a layer depth change")

    # A change inside the tolerance keeps the history.
    f_small = {"size": [8, 6], "jitter": [0, 0],
               "layers": [dict(layer, depth=[0.31, 0.001, 0.0])]}
    engine_small = taa.LayerTemporal()
    out_small = [engine_small.step(f, True) for f in (f0, f_small)]
    small_layer_w = [out_small[1]["sample_weights"][y][x][k]
                     for y in range(6) for x in range(8) for k in range(4)
                     if out_small[1]["sample_ids"][y][x][k] == 1]
    check(bool(small_layer_w) and all(w > 0 for w in small_layer_w),
          "depth change within 0.025 keeps history")

    # Gradient depth planes are evaluated at the history tap's local point.
    f_grad = {"size": [8, 6], "jitter": [0, 0],
              "layers": [dict(layer, depth=[0.30, 0.02, 0.01])]}
    engine_grad = taa.LayerTemporal()
    out_grad = [engine_grad.step(f, True) for f in (f0, f_grad)]
    grad_reasons = {r for row in out_grad[1]["sample_reasons"] for cell in row
                    for r in cell}
    check("depth_mismatch" in grad_reasons,
          "tilted depth planes are compared at local coordinates")

    # Equal depth resolves to the smaller id.
    tie = {"size": [6, 5], "jitter": [0, 0], "layers": [
        {"id": 3, "size": [4, 4], "transform": [1, 0, 0, 1, 0.5, 0.5],
         "depth": [0.4, 0, 0], "color": 0.6},
        {"id": 1, "size": [4, 4], "transform": [1, 0, 0, 1, 0.5, 0.5],
         "depth": [0.4, 0, 0], "color": 0.2}]}
    tie_out = taa.LayerTemporal().step(tie, True)
    overlap_ids = {tie_out["sample_ids"][2][2][k] for k in range(4)}
    check(overlap_ids == {1}, "equal depth resolves to the ascending id")
    check(all(abs(c - 0.2) < 1e-12 for c in tie_out["sample_colors"][2][2]),
          "equal-depth winner supplies the linear colour")


def test_layer_cli_outputs():
    print("\n== layer scene: CLI contract and exported fields ==")
    frames = build_layer_sequence()[:3]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        scene_path = tmp / "layers.json"
        scene_path.write_text(json.dumps({"frames": frames}))
        out_path = tmp / "out.json"
        subprocess.run([sys.executable, "taa.py", str(scene_path),
                        str(out_path)], check=True)
        data = json.loads(out_path.read_text())["frames"]
    required = {"color", "weights", "reasons", "sample_ids",
                "sample_colors", "sample_weights", "history_usage",
                "rejection_counts"}
    check(required.issubset(data[0].keys()),
          "per-frame and per-sample export fields present")
    shapes_ok = all(
        len(f["sample_ids"][y][x]) == 4 and
        len(f["sample_colors"][y][x]) == 4 and
        len(f["sample_weights"][y][x]) == 4
        for f in data
        for y in range(f["sample_ids"] and len(f["sample_ids"]))
        for x in range(len(f["sample_ids"][0])))
    check(shapes_ok, "per-sample arrays have the fixed 2x2 shape")


def main():
    test_legacy_quality()
    test_legacy_moving_occlusion()
    test_legacy_cli_and_replay()
    test_layer_geometry_and_coverage()
    test_layer_temporal()
    test_layer_all_invalid_footprint()
    test_layer_bilinear_normalization()
    test_layer_depth_rules()
    test_layer_cli_outputs()
    print("\n" + ("ALL CHECKS PASSED" if not FAILURES
                  else f"{len(FAILURES)} CHECK(S) FAILED: {FAILURES}"))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
