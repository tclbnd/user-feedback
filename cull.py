"""Offline conservative instance occlusion culling.

The renderer keeps the draw rules of the original reference renderer
(``reference_render``).  Culling is performed by a conservative multi-scale
(max-reduction) depth hierarchy built from opaque occluders only.

Key conservative guarantees (a false cull is never allowed):

* A bounding volume that pierces the near plane is always kept.
* Only volumes fully behind the near plane, or whose projection provably has
  no pixel centre on screen, can be discarded without a depth query.
* Screen query rectangles are grown to whole pixels (floor/ceil), which can
  only keep more instances.
* A hierarchy tile whose footprint contains any uncovered (infinite depth)
  pixel stores infinity, so a partially covered tile can never prove
  occlusion.
* Equal depths resolve to "keep".
* Transparent occluders never write depth and therefore cannot cull.

Per-frame geometry is the only source of occlusion evidence.  The previous
frame is kept solely as a reusable hint; when its signature does not match the
current frame the pipeline falls back to a full recomputation.
"""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INF = math.inf


# --------------------------------------------------------------------------- #
# Reference renderer (unchanged draw rules, used as the independent baseline).
# --------------------------------------------------------------------------- #
def project(box, width, height, near):
    """Project a camera-space box exactly as the baseline renderer does."""
    x0, y0, z0, x1, y1, z1 = box
    if z1 < near:
        return None
    distances = [max(near, z0), z1]
    xs = [width / 2 + (width / 2) * x / z for x in (x0, x1) for z in distances]
    ys = [height / 2 + (height / 2) * y / z for y in (y0, y1) for z in distances]
    return min(xs), min(ys), max(xs), max(ys)


def reference_render(instance_ids, instances, frame, width, height, near):
    """Full rasterisation producing per-pixel instance IDs (the ground truth).

    Opaque occluders initialise the depth buffer; transparent ones are
    ignored.  Instances draw in ascending id order, pixel centres at
    ``x + 0.5, y + 0.5`` with a half-open rectangle and strict depth test.
    """
    depths = [[INF] * width for _ in range(height)]
    image = [[None] * width for _ in range(height)]
    for occ in frame["occluders"]:
        if not occ["opaque"]:
            continue
        x0, y0, x1, y1 = occ["rect"]
        for y in range(max(0, y0), min(y1, height)):
            for x in range(max(0, x0), min(x1, width)):
                if occ["z"] < depths[y][x]:
                    depths[y][x] = occ["z"]
    for inst in sorted(instances, key=lambda i: i["id"]):
        if inst["id"] not in instance_ids:
            continue
        p = project(inst["box"], width, height, near)
        if p is None:
            continue
        a, b, c, d = p
        z = max(near, inst["box"][2])
        for y in range(max(0, math.floor(b)), min(height, math.ceil(d))):
            for x in range(max(0, math.floor(a)), min(width, math.ceil(c))):
                if a <= x + .5 < c and b <= y + .5 < d and z < depths[y][x]:
                    image[y][x] = inst["id"]
                    depths[y][x] = z
    return image


# Backwards-compatible alias for the original renderer name.
render = reference_render


# --------------------------------------------------------------------------- #
# Conservative projection and multi-scale max-depth hierarchy.
# --------------------------------------------------------------------------- #
def classify_box(box, width, height, near):
    """Classify a camera-space box conservatively.

    Returns ``(kind, screen_rect, min_depth)`` where ``kind`` is one of
    ``"behind_near"``, ``"near_piercing"``, ``"offscreen"`` or ``"query"``.
    ``screen_rect`` is a half-open integer pixel rectangle for ``"query"``.
    """
    x0, y0, z0, x1, y1, z1 = box
    if z1 < near:
        return "behind_near", None, INF
    if z0 < near:
        # The volume crosses the near plane: its clipped projection is
        # unbounded, so no finite screen query can prove occlusion.
        return "near_piercing", None, max(near, z0)

    # All four (x, z) / (y, z) corner combinations are evaluated; with an
    # arbitrary sign for x/y the projected extrema need not sit on a box edge
    # that uses only two depth values.
    xs = [width / 2 + (width / 2) * x / z
          for x in (x0, x1) for z in (z0, z1)]
    ys = [height / 2 + (height / 2) * y / z
          for y in (y0, y1) for z in (z0, z1)]
    px0, px1 = min(xs), max(xs)
    py0, py1 = min(ys), max(ys)

    # Grow to whole pixels.  A pixel is covered when its centre lies in the
    # half-open projected rectangle.
    qx0 = max(0, math.floor(px0))
    qy0 = max(0, math.floor(py0))
    qx1 = min(width, math.ceil(px1))
    qy1 = min(height, math.ceil(py1))
    if qx0 >= qx1 or qy0 >= qy1:
        return "offscreen", None, z0
    return "query", (qx0, qy0, qx1, qy1), z0


def build_depth_pyramid(frame, width, height):
    """Build level 0 from opaque occluders, then max-reduce upwards.

    Background stays at infinity, hence every tile touching an uncovered
    pixel remains infinite and cannot certify occlusion.  Tiles on the image
    border simply cover the pixels that exist; absent neighbours are outside
    every clamped query and are not treated as coverage holes.
    """
    level0 = [INF] * (width * height)
    for occ in frame["occluders"]:
        if not occ["opaque"]:
            continue
        x0, y0, x1, y1 = occ["rect"]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        z = occ["z"]
        for y in range(y0, y1):
            base = y * width
            for x in range(x0, x1):
                idx = base + x
                if z < level0[idx]:
                    level0[idx] = z

    pyramid = [level0]
    dims = [(width, height)]
    w, h = width, height
    while w > 1 or h > 1:
        prev = pyramid[-1]
        pw, ph = w, h
        nw, nh = max(1, w // 2), max(1, h // 2)
        cur = [INF] * (nw * nh)
        for ny in range(nh):
            for nx in range(nw):
                worst = -INF
                for sy in range(2):
                    sy_abs = 2 * ny + sy
                    if sy_abs >= ph:
                        continue
                    row = sy_abs * pw
                    for sx in range(2):
                        sx_abs = 2 * nx + sx
                        if sx_abs >= pw:
                            continue
                        v = prev[row + sx_abs]
                        if v > worst:
                            worst = v
                cur[ny * nw + nx] = worst if worst != -INF else INF
        pyramid.append(cur)
        dims.append((nw, nh))
        w, h = nw, nh
    return pyramid, dims


def _tile_level_for_span(span_x, span_y, dims):
    """Smallest level whose tile footprint covers both query spans.

    Frame dimensions need not be powers of two, so a level's tile footprint
    is derived from the actual level dimensions, never assumed to be exactly
    ``2**level``.  Clamping to the top level guarantees every tile index
    stays inside that level's array.
    """
    for level, (lw, lh) in enumerate(dims):
        base_w, base_h = dims[0]
        tile_w = max(1, base_w // lw)
        tile_h = max(1, base_h // lh)
        if tile_w >= span_x and tile_h >= span_y:
            return level
    return len(dims) - 1


def hierarchy_occluded(pyramid, dims, rect, min_depth):
    """Conservative occlusion query.

    Returns ``(occluded, query_count)``.  All hierarchy tiles overlapping the
    grown pixel rectangle are sampled; the query may only certify occlusion
    when the farthest stored depth among them is strictly nearer than the
    volume's nearest possible depth.  Equality keeps the instance.
    """
    x0, y0, x1, y1 = rect
    span_x, span_y = x1 - x0, y1 - y0
    level = _tile_level_for_span(span_x, span_y, dims)
    lw, lh = dims[level]
    base_w, base_h = dims[0]
    tile_w = max(1, base_w // lw)
    tile_h = max(1, base_h // lh)
    data = pyramid[level]

    tx0 = x0 // tile_w
    ty0 = y0 // tile_h
    tx1 = (x1 - 1) // tile_w
    ty1 = (y1 - 1) // tile_h

    worst = -INF
    queries = 0
    for ty in range(ty0, min(ty1 + 1, lh)):
        base = ty * lw
        for tx in range(tx0, min(tx1 + 1, lw)):
            v = data[base + tx]
            queries += 1
            if v > worst:
                worst = v
    # worst == infinity whenever a touched tile has an uncovered pixel.
    return worst < min_depth, queries


# --------------------------------------------------------------------------- #
# Per-instance culling and stable batching.
# --------------------------------------------------------------------------- #
def cull_instance(inst, pyramid, dims, width, height, near):
    """Return ``(kept, reason, query_count)`` for one instance."""
    kind, rect, min_depth = classify_box(inst["box"], width, height, near)
    if kind == "behind_near":
        return False, "behind_near", 0
    if kind == "near_piercing":
        return True, "near_piercing", 0
    if kind == "offscreen":
        return False, "offscreen", 0
    occluded, queries = hierarchy_occluded(pyramid, dims, rect, min_depth)
    if occluded:
        return False, "occluded", queries
    return True, "visible", queries


def _split_blocks(count, workers):
    workers = max(1, min(workers, count) if count else 1)
    base = count // workers
    extra = count % workers
    blocks = []
    start = 0
    for w in range(workers):
        size = base + (1 if w < extra else 0)
        if size:
            blocks.append((start, start + size))
        start += size
    return blocks


def stable_batches(instances, kept_ids):
    """Group kept instances by material with fully sorted, id-stable output."""
    kept_set = set(kept_ids)
    groups = {}
    for inst in sorted(instances, key=lambda i: i["id"]):
        if inst["id"] in kept_set:
            groups.setdefault(inst["material"], []).append(inst["id"])
    return [{"material": m, "ids": groups[m]} for m in sorted(groups)]


def frame_signature(frame, width, height, near):
    """Order-independent fingerprint of everything affecting occlusion."""
    return json.dumps(
        {"size": [width, height], "near": near,
         "occluders": sorted(
             ([o["rect"][0], o["rect"][1], o["rect"][2], o["rect"][3]],
              o["z"], bool(o["opaque"])) for o in frame["occluders"]),
         "instances": sorted(
             (i["id"], i["material"], i["box"]) for i in frame["instances"])},
        sort_keys=True, separators=(",", ":"))


def cull_frame(frame, width, height, near, workers=1, traversal="forward",
               previous=None):
    """Cull one frame.

    ``previous`` is an optional reusable hint from the preceding frame:
    ``{"signature", "kept", "batches"}``.  A signature mismatch (the normal
    case under motion) triggers a complete recomputation fallback.
    """
    signature = frame_signature(frame, width, height, near)
    if previous is not None and previous.get("signature") == signature:
        kept = list(previous["kept"])
        batches = [{"material": b["material"], "ids": list(b["ids"])}
                   for b in previous["batches"]]
        image = reference_render(set(kept), frame["instances"], frame,
                                 width, height, near)
        return {
            "kept": kept,
            "batches": batches,
            "ids": image,
            "stats": {"hierarchy_queries": 0, "reused_previous": True,
                      "fallback_recompute": False, "workers": workers,
                      "blocks": 0,
                      "reasons": {"visible": len(kept), "occluded": 0,
                                  "near_piercing": 0, "offscreen": 0,
                                  "behind_near": 0}},
        }, {"signature": signature, "kept": kept, "batches": batches}

    pyramid, dims = build_depth_pyramid(frame, width, height)
    instances = frame["instances"]
    n = len(instances)
    kept_flags = [False] * n
    reasons = {"visible": 0, "occluded": 0, "near_piercing": 0,
               "offscreen": 0, "behind_near": 0}
    total_queries = 0

    blocks = _split_blocks(n, workers)
    if traversal == "reverse":
        blocks = list(reversed(blocks))

    def process(block):
        lo, hi = block
        local_flags = [False] * (hi - lo)
        local_reasons = {}
        local_queries = 0
        for k in range(lo, hi):
            kept, reason, q = cull_instance(
                instances[k], pyramid, dims, width, height, near)
            local_flags[k - lo] = kept
            local_reasons[reason] = local_reasons.get(reason, 0) + 1
            local_queries += q
        return lo, hi, local_flags, local_reasons, local_queries

    if workers <= 1 or n <= 1:
        results = [process(b) for b in blocks]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(process, blocks))

    # Merge in instance-index order regardless of how blocks were scheduled.
    for lo, hi, flags, local_reasons, q in results:
        for k in range(lo, hi):
            kept_flags[k] = flags[k - lo]
        for reason, count in local_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
        total_queries += q

    kept = sorted(instances[k]["id"]
                  for k in range(n) if kept_flags[k])
    batches = stable_batches(instances, kept)
    image = reference_render(set(kept), frame["instances"], frame,
                             width, height, near)
    hint = {"signature": signature, "kept": kept, "batches": batches}
    return {
        "kept": kept,
        "batches": batches,
        "ids": image,
        "stats": {"hierarchy_queries": total_queries,
                  "reused_previous": False,
                  "fallback_recompute": previous is not None,
                  "workers": workers, "blocks": len(blocks),
                  "reasons": reasons},
    }, hint


def draw(scene, no_cull=False, workers=1, traversal="forward"):
    """Run the whole sequence, carrying a reusable hint between frames."""
    width, height = scene["size"]
    near = scene["near"]
    result = []
    previous = None
    for frame in scene["frames"]:
        if no_cull:
            kept = sorted(i["id"] for i in frame["instances"])
            batches = stable_batches(frame["instances"], kept)
            image = reference_render(set(kept), frame["instances"], frame,
                                     width, height, near)
            result.append({"kept": kept, "batches": batches, "ids": image,
                           "stats": {"hierarchy_queries": 0,
                                     "reused_previous": False,
                                     "fallback_recompute": False,
                                     "workers": workers, "blocks": 0,
                                     "reasons": {"visible": len(kept),
                                                 "occluded": 0,
                                                 "near_piercing": 0,
                                                 "offscreen": 0,
                                                 "behind_near": 0}}})
            continue
        frame_result, previous = cull_frame(
            frame, width, height, near, workers=workers,
            traversal=traversal, previous=previous)
        result.append(frame_result)
    return {"frames": result}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("scene", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--no-cull", action="store_true")
    p.add_argument("--reverse", action="store_true",
                   help="reverse block traversal order (determinism check)")
    a = p.parse_args()
    scene = json.loads(a.scene.read_text())
    result = draw(scene, no_cull=a.no_cull, workers=max(1, a.workers),
                  traversal="reverse" if a.reverse else "forward")
    a.output.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
