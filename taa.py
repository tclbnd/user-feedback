"""Offline temporal reconstruction.

Two scene formats share the same CLI:

* legacy scenes (``camera`` / ``object``): one moving foreground quad over a
  procedural stripe background, sampled at pixel centres;
* multi-layer scenes (``layers``): fixed 2x2 sub-pixel samples, an affine
  screen-to-world ``view`` and several independently transformed depth layers.

Both paths re-project every current sample to the previous frame using the same
object-local point, validate the bilinear history footprint (bounds, object id,
predicted depth), renormalise over the surviving footprint and only blend
neighbours belonging to the same object.  Cut shots, viewport changes and the
first frame always reset history; --no-history renders the current frame only.
"""
import argparse
import json
import math
from pathlib import Path


HISTORY_ALPHA = 0.8
DEPTH_TOLERANCE = 0.025
EPS = 1e-12

# Per-pixel / per-sample reason codes.
REASON_DISABLED = "disabled"
REASON_FIRST = "first_frame"
REASON_CUT = "cut"
REASON_SIZE = "size_change"
REASON_OOB = "out_of_bounds"
REASON_ID = "id_mismatch"
REASON_DEPTH = "depth_mismatch"
REASON_ACCEPTED = "accepted"


def mean(values):
    values = list(values)
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Small affine helpers.  A 2D affine transform is the flat list
# [a, b, c, d, tx, ty] mapping (u, v) -> (a*u + b*v + tx, c*u + d*v + ty).
# ---------------------------------------------------------------------------
def affine_apply(m, u, v):
    return (m[0] * u + m[1] * v + m[4],
            m[2] * u + m[3] * v + m[5])


def affine_inverse(m):
    a, b, c, d, tx, ty = m
    det = a * d - b * c
    inv = 1.0 / det
    return [d * inv, -b * inv, -c * inv, a * inv,
            (b * ty - d * tx) * inv, (c * tx - a * ty) * inv]


def background(x, y):
    """Legacy procedural background, kept identical to the original renderer."""
    stripe = math.floor(1.75 * x + 0.25 * y) % 2
    return 0.32 + 0.26 * stripe


def bilinear_footprint(grid_w, grid_h, qx, qy, origin=0.25, spacing=0.5):
    """Bilinear footprint of query (qx, qy) on a uniform sample grid.

    The multi-layer history grid uses spacing 0.5 with its first node at
    0.25; the legacy temporal path uses an 8x8 sub-pixel grid (spacing
    0.125, origin 0.0625).  Returns four (i, j, weight) tuples; zero-weight
    taps are kept so callers can attribute a rejection reason, but they
    contribute nothing after the renormalisation over surviving taps.
    """
    fx = (qx - origin) / spacing
    fy = (qy - origin) / spacing
    i0 = math.floor(fx)
    j0 = math.floor(fy)
    tx = fx - i0
    ty = fy - j0
    return ((i0, j0, (1.0 - tx) * (1.0 - ty)),
            (i0 + 1, j0, tx * (1.0 - ty)),
            (i0, j0 + 1, (1.0 - tx) * ty),
            (i0 + 1, j0 + 1, tx * ty))


def classify_tap(in_bounds, same_id, depth_ok):
    """Deterministic rejection precedence for a single history tap."""
    if not in_bounds:
        return REASON_OOB
    if not same_id:
        return REASON_ID
    if not depth_ok:
        return REASON_DEPTH
    return REASON_ACCEPTED


# ---------------------------------------------------------------------------
# Legacy scene: pixel-centre samples, one foreground quad.
# ---------------------------------------------------------------------------
def render(frame):
    """Original renderer contract: pixel-centre samples, foreground id 1."""
    width, height = frame["size"]
    camera_x, camera_y = frame["camera"]
    object_x, object_y = frame["object"]
    jitter_x, jitter_y = frame["jitter"]
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            world_x = x + 0.5 + camera_x + jitter_x
            world_y = y + 0.5 + camera_y + jitter_y
            inside = (object_x <= world_x < object_x + 5
                      and object_y <= world_y < object_y + 4)
            if inside:
                row.append({"id": 1, "z": 0.24, "color": 0.87})
            else:
                row.append({"id": 0, "z": 0.80,
                            "color": background(world_x, world_y)})
        pixels.append(row)
    return pixels


# Internal sub-pixel grid used by the legacy temporal path: 8x8 samples per
# pixel, spacing 0.125, first node at 0.0625 (the same grid a 64-sample
# reference uses).
LEGACY_SUB = 8
LEGACY_SPACING = 1.0 / LEGACY_SUB
LEGACY_ORIGIN = 0.5 * LEGACY_SPACING


def render_legacy_subsamples(frame):
    """8x8 sub-pixel samples with id, depth, linear colour and world point."""
    width, height = frame["size"]
    cam_x, cam_y = frame["camera"]
    obj_x, obj_y = frame["object"]
    jx, jy = frame["jitter"]
    grid = []
    for y in range(height):
        grid_row = []
        for x in range(width):
            cell = []
            for sj in range(LEGACY_SUB):
                for si in range(LEGACY_SUB):
                    off_x = (si + 0.5) * LEGACY_SPACING
                    off_y = (sj + 0.5) * LEGACY_SPACING
                    wx = x + off_x + cam_x + jx
                    wy = y + off_y + cam_y + jy
                    if obj_x <= wx < obj_x + 5 and obj_y <= wy < obj_y + 4:
                        cell.append({"id": 1, "z": 0.24, "color": 0.87,
                                     "world": (wx, wy)})
                    else:
                        cell.append({"id": 0, "z": 0.80,
                                     "color": background(wx, wy),
                                     "world": (wx, wy)})
            grid_row.append(cell)
        grid.append(grid_row)
    return grid


class Temporal:
    """Temporal accumulator for the legacy one-quad scene.

    History is accumulated on an internal 8x8 sub-pixel grid: every current
    sub-sample is re-projected through the object/camera motion, its bilinear
    footprint is validated by object id and depth, renormalised over surviving
    taps and blended only with same-object neighbours.  The exported colour is
    the pixel mean of those linear reconstructions.
    """

    def __init__(self):
        self.previous = None
        self.prev_frame = None

    def reset_reason(self, frame, enabled):
        if not enabled:
            return REASON_DISABLED
        if self.previous is None:
            # A cut stays a cut even when the replay starts at that frame, so
            # truncated replay from a cut point is byte-for-byte identical.
            return REASON_CUT if frame.get("cut") else REASON_FIRST
        if frame.get("cut"):
            return REASON_CUT
        if self.prev_frame["size"] != frame["size"]:
            return REASON_SIZE
        return None

    def step(self, frame, enabled=True):
        grid = render_legacy_subsamples(frame)
        height = len(grid)
        width = len(grid[0])
        n_sub = LEGACY_SUB * LEGACY_SUB
        colors = [[[s["color"] for s in cell] for cell in row]
                  for row in grid]
        weights = [[[0.0] * n_sub for _ in range(width)]
                   for _ in range(height)]
        reasons = [[[REASON_FIRST] * n_sub for _ in range(width)]
                   for _ in range(height)]

        reset = self.reset_reason(frame, enabled)
        if reset is not None:
            for y in range(height):
                for x in range(width):
                    for k in range(n_sub):
                        reasons[y][x][k] = reset
            if enabled:
                # The current frame becomes the baseline of the new segment.
                self.previous = grid
                self.prev_frame = frame
            return self._assemble(colors, weights, reasons, reset)

        old = self.previous
        old_frame = self.prev_frame
        grid_w = width * LEGACY_SUB
        grid_h = height * LEGACY_SUB
        cam_x, cam_y = frame["camera"]
        jx, jy = frame["jitter"]
        old_cam_x, old_cam_y = old_frame["camera"]
        old_ox, old_oy = old_frame["object"]
        ox, oy = frame["object"]

        for y in range(height):
            for x in range(width):
                for k, cur in enumerate(grid[y][x]):
                    wx, wy = cur["world"]
                    if cur["id"] == 1:
                        # Same object-local point reprojected into old frame.
                        local_x = wx - ox
                        local_y = wy - oy
                        qx = old_ox + local_x - old_cam_x
                        qy = old_oy + local_y - old_cam_y
                    else:
                        # Background is stationary: reproject along world point.
                        qx = wx - old_cam_x
                        qy = wy - old_cam_y

                    valid = []
                    rejection = REASON_OOB
                    for gi, gj, tw in bilinear_footprint(
                            grid_w, grid_h, qx, qy,
                            origin=LEGACY_ORIGIN, spacing=LEGACY_SPACING):
                        in_bounds = 0 <= gi < grid_w and 0 <= gj < grid_h
                        if in_bounds:
                            tap = old[gj // LEGACY_SUB][gi // LEGACY_SUB][
                                (gj % LEGACY_SUB) * LEGACY_SUB
                                + (gi % LEGACY_SUB)]
                            same_id = tap["id"] == cur["id"]
                            depth_ok = abs(tap["z"] - cur["z"]) <= \
                                DEPTH_TOLERANCE
                        else:
                            same_id = depth_ok = False
                        verdict = classify_tap(in_bounds, same_id, depth_ok)
                        if verdict != REASON_ACCEPTED and tw > EPS \
                                and rejection == REASON_OOB:
                            rejection = verdict
                        if verdict == REASON_ACCEPTED and tw > EPS:
                            valid.append((gi, gj, tw))

                    coverage = sum(w for _, _, w in valid)
                    if coverage <= EPS:
                        # No same-object history: keep the current colour so a
                        # disoccluded sample never inherits the foreground.
                        reasons[y][x][k] = rejection
                        continue

                    total = sum(w for _, _, w in valid)
                    hist_color = 0.0
                    for gi, gj, tw in valid:
                        tap = old[gj // LEGACY_SUB][gi // LEGACY_SUB][
                            (gj % LEGACY_SUB) * LEGACY_SUB
                            + (gi % LEGACY_SUB)]
                        hist_color += tap["color"] * tw
                    hist_color /= total
                    # Only same-object neighbours survive the id test; blend
                    # strength decays with the usable footprint coverage.
                    alpha = HISTORY_ALPHA * min(1.0, coverage)
                    colors[y][x][k] = (
                        cur["color"] * (1.0 - alpha) + hist_color * alpha)
                    weights[y][x][k] = alpha
                    reasons[y][x][k] = REASON_ACCEPTED

        self.previous = grid
        self.prev_frame = frame
        return self._assemble(colors, weights, reasons, None)

    def _assemble(self, sub_colors, sub_weights, sub_reasons, reset):
        height = len(sub_colors)
        width = len(sub_colors[0])
        color = [[mean(sub_colors[y][x]) for x in range(width)]
                 for y in range(height)]
        weights = [[mean(sub_weights[y][x]) for x in range(width)]
                   for y in range(height)]
        reasons = []
        for y in range(height):
            row = []
            for x in range(width):
                sr = sub_reasons[y][x]
                if all(r == REASON_ACCEPTED for r in sr):
                    row.append(REASON_ACCEPTED)
                elif any(r == REASON_ACCEPTED for r in sr):
                    row.append("partial_history")
                else:
                    row.append(sr[0])
            reasons.append(row)

        total = width * height * len(sub_colors[0][0])
        used = sum(value for row in sub_weights for cell in row
                   for value in cell)
        counts = {}
        for row in sub_reasons:
            for cell in row:
                for reason in cell:
                    counts[reason] = counts.get(reason, 0) + 1
        return {
            "color": color,
            "weights": weights,
            "reasons": reasons,
            "sample_weights": sub_weights,
            "sample_reasons": sub_reasons,
            "history_usage": used / total if total else 0.0,
            "rejection_counts": {k: v for k, v in counts.items()
                                 if k != REASON_ACCEPTED},
            "reset": reset,
        }


# ---------------------------------------------------------------------------
# Multi-layer scene: fixed 2x2 sub-pixel samples with an affine view.
# ---------------------------------------------------------------------------
SAMPLE_OFFSETS = ((0.25, 0.25), (0.75, 0.25),
                  (0.25, 0.75), (0.75, 0.75))


def _layer_depth(layer, u, v):
    z0, zx, zy = layer["depth"]
    return z0 + zx * u + zy * v


def render_layer_frame(frame):
    """Render the 2x2 samples.  Each sample stores the visible id, linear
    colour, depth and object-local coordinates used for reprojection."""
    width, height = frame["size"]
    jx, jy = frame["jitter"]
    view = frame.get("view", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    layers = frame["layers"]
    inverse_views = [affine_inverse(layer["transform"]) for layer in layers]

    grid = []
    for y in range(height):
        grid_row = []
        for x in range(width):
            samples = []
            for sx, sy in SAMPLE_OFFSETS:
                screen_u = x + sx + jx
                screen_v = y + sy + jy
                world_x, world_y = affine_apply(view, screen_u, screen_v)
                # Background id 0 at depth 0.8; local point is the world point.
                candidates = [(0, 0.80, background(world_x, world_y),
                               world_x, world_y)]
                for layer, inv_t in zip(layers, inverse_views):
                    lu, lv = affine_apply(inv_t, world_x, world_y)
                    lw, lh = layer["size"]
                    if 0.0 <= lu < lw and 0.0 <= lv < lh:
                        candidates.append((layer["id"],
                                           _layer_depth(layer, lu, lv),
                                           layer["color"], lu, lv))
                # Smaller depth wins; equal depth resolves by ascending id.
                candidates.sort(key=lambda c: (c[1], c[0]))
                obj_id, z, col, lu, lv = candidates[0]
                samples.append({"id": obj_id, "z": z, "color": col,
                                "local": (lu, lv),
                                "screen": (screen_u, screen_v)})
            grid_row.append(samples)
        grid.append(grid_row)
    return grid


class LayerTemporal:
    """Temporal accumulator for the multi-object sub-pixel scene."""

    def __init__(self):
        self.previous = None
        self.prev_frame = None

    def reset_reason(self, frame, enabled):
        if not enabled:
            return REASON_DISABLED
        if self.previous is None:
            # Preserve the cut marker across replays that begin at this frame.
            return REASON_CUT if frame.get("cut") else REASON_FIRST
        if frame.get("cut"):
            return REASON_CUT
        if self.prev_frame["size"] != frame["size"]:
            return REASON_SIZE
        return None

    def step(self, frame, enabled=True):
        grid = render_layer_frame(frame)
        height = len(grid)
        width = len(grid[0])
        view = frame.get("view", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        inv_view = affine_inverse(view)

        sample_ids = [[[s["id"] for s in row] for row in grid_row]
                      for grid_row in grid]
        sample_colors = [[[s["color"] for s in row] for row in grid_row]
                         for grid_row in grid]
        sample_weights = [[[0.0] * 4 for _ in range(width)]
                          for _ in range(height)]
        sample_reasons = [[[REASON_FIRST] * 4 for _ in range(width)]
                          for _ in range(height)]

        reset = self.reset_reason(frame, enabled)
        if reset is not None:
            for y in range(height):
                for x in range(width):
                    for k in range(4):
                        sample_reasons[y][x][k] = reset
            if enabled:
                # New segment baseline: weights stay zero on the reset frame.
                self.previous = grid
                self.prev_frame = frame
            return self._assemble(sample_ids, sample_colors, sample_weights,
                                  sample_reasons, reset)

        old = self.previous
        old_frame = self.prev_frame
        old_view = old_frame.get("view", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        old_inv_view = affine_inverse(old_view)
        old_layers = {layer["id"]: layer for layer in old_frame["layers"]}
        cur_layers = {layer["id"]: layer for layer in frame["layers"]}
        old_jx, old_jy = old_frame["jitter"]
        # History source grid: 2 samples per pixel, spacing 0.5, origin 0.25.
        grid_w = width * 2
        grid_h = height * 2

        for y in range(height):
            for x in range(width):
                for k, cur in enumerate(grid[y][x]):
                    lu, lv = cur["local"]
                    if cur["id"] == 0:
                        # Background is stationary: follow the world point back
                        # through the previous view, removing old jitter.
                        wx, wy = lu, lv
                        hu, hv = affine_apply(old_inv_view, wx, wy)
                    else:
                        old_layer = old_layers.get(cur["id"])
                        if old_layer is None or \
                                cur["id"] not in cur_layers:
                            sample_reasons[y][x][k] = REASON_ID
                            continue
                        # Same object-local point under the previous transform.
                        wx, wy = affine_apply(old_layer["transform"], lu, lv)
                        hu, hv = affine_apply(old_inv_view, wx, wy)
                    qx = hu - old_jx
                    qy = hv - old_jy

                    valid = []
                    rejection = REASON_OOB
                    for gi, gj, tw in bilinear_footprint(grid_w, grid_h,
                                                         qx, qy):
                        in_bounds = 0 <= gi < grid_w and 0 <= gj < grid_h
                        if in_bounds:
                            tap = old[gj // 2][gi // 2][(gj % 2) * 2
                                                        + (gi % 2)]
                            same_id = tap["id"] == cur["id"]
                            if cur["id"] == 0:
                                z_expected = 0.80
                            else:
                                # Predicted depth: the current frame's plane
                                # evaluated at the history tap's local point.
                                z_expected = _layer_depth(
                                    cur_layers[cur["id"]],
                                    tap["local"][0], tap["local"][1])
                            depth_ok = abs(tap["z"] - z_expected) <= \
                                DEPTH_TOLERANCE
                        else:
                            same_id = depth_ok = False
                        verdict = classify_tap(in_bounds, same_id, depth_ok)
                        if verdict != REASON_ACCEPTED and tw > EPS \
                                and rejection == REASON_OOB:
                            rejection = verdict
                        if verdict == REASON_ACCEPTED and tw > EPS:
                            valid.append((gi, gj, tw))

                    coverage = sum(w for _, _, w in valid)
                    if coverage <= EPS:
                        # Every tap failed: zero history weight, current colour
                        # only, so another layer can never bleed through.
                        sample_reasons[y][x][k] = rejection
                        continue

                    total = sum(w for _, _, w in valid)
                    hist_color = 0.0
                    for gi, gj, tw in valid:
                        tap = old[gj // 2][gi // 2][(gj % 2) * 2 + (gi % 2)]
                        hist_color += tap["color"] * tw
                    hist_color /= total

                    # Neighbourhood constraint: all surviving taps share the
                    # current id; blend strength decays with valid coverage.
                    alpha = HISTORY_ALPHA * min(1.0, coverage)
                    sample_colors[y][x][k] = (
                        cur["color"] * (1.0 - alpha) + hist_color * alpha)
                    sample_weights[y][x][k] = alpha
                    sample_reasons[y][x][k] = REASON_ACCEPTED

        self.previous = grid
        self.prev_frame = frame
        return self._assemble(sample_ids, sample_colors, sample_weights,
                              sample_reasons, None)

    def _assemble(self, sample_ids, sample_colors, sample_weights,
                  sample_reasons, reset):
        height = len(sample_colors)
        width = len(sample_colors[0])
        color = [[sum(sample_colors[y][x]) / 4.0 for x in range(width)]
                 for y in range(height)]
        weights = [[sum(sample_weights[y][x]) / 4.0 for x in range(width)]
                   for y in range(height)]
        reasons = []
        for y in range(height):
            row = []
            for x in range(width):
                sr = sample_reasons[y][x]
                if all(r == REASON_ACCEPTED for r in sr):
                    row.append(REASON_ACCEPTED)
                elif any(r == REASON_ACCEPTED for r in sr):
                    row.append("partial_history")
                else:
                    row.append(sr[0])
            reasons.append(row)

        total = width * height * 4
        used = sum(value for row in sample_weights for cell in row
                   for value in cell)
        counts = {}
        for row in sample_reasons:
            for cell in row:
                for reason in cell:
                    counts[reason] = counts.get(reason, 0) + 1
        return {
            "color": color,
            "weights": weights,
            "reasons": reasons,
            "sample_ids": sample_ids,
            "sample_colors": sample_colors,
            "sample_weights": sample_weights,
            "sample_reasons": sample_reasons,
            "history_usage": used / total if total else 0.0,
            "rejection_counts": {k: v for k, v in counts.items()
                                 if k != REASON_ACCEPTED},
            "reset": reset,
        }


def is_layer_scene(frames):
    return all("layers" in frame for frame in frames)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scene", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.scene.read_text())
    frames_in = data["frames"]
    if is_layer_scene(frames_in):
        engine = LayerTemporal()
    else:
        engine = Temporal()
    frames = [engine.step(frame, not args.no_history)
              for frame in frames_in]
    args.output.write_text(json.dumps({"frames": frames},
                                      allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
