"""Offline instance renderer with conservative multi-scale occlusion culling.

The reference drawing rules live in :func:`render`; culling must never remove an
instance that would have contributed a pixel there.  Occlusion is proved only
from opaque occluder depth using a max-depth hierarchy.  A max (not min)
hierarchy is essential: a coarse tile proves occlusion only when *every* pixel
below it is strictly nearer than the instance.  Thin occluders therefore survive
tile reduction, and a single uncovered pixel prevents a cull.
"""
import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INF = float("inf")


def project(box, width, height, near):
    """Bounding projection of a camera-space box, as used by :func:`render`.

    The reference renderer samples only the two end planes (``max(near, z0)``
    and ``z1``) and takes the extremes; that is kept identical here so culling
    and drawing agree on the footprint.  Returns ``None`` when the whole box
    lies behind the near plane.
    """
    x0, y0, z0, x1, y1, z1 = box
    if z1 < near:
        return None
    distances = [max(near, z0), z1]
    xs = [width / 2 + (width / 2) * x / z for x in (x0, x1) for z in distances]
    ys = [height / 2 + (height / 2) * y / z for y in (y0, y1) for z in distances]
    return min(xs), min(ys), max(xs), max(ys)


def render(instance_ids, instances, frame, width, height, near):
    """Reference per-pixel render: nearest opaque occluder, then instances."""
    depths = [[math.inf] * width for _ in range(height)]
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


class DepthHierarchy:
    """Multi-scale max-depth pyramid over the opaque occluder depth buffer.

    Level 0 is the per-pixel depth; each higher level stores the maximum depth
    of its (image-clipped) 2x2 child tile.  A tile with max < z proves every
    pixel in it is strictly nearer, hence occluded.  A tile containing even one
    uncovered pixel has an infinite max and forces descent, so thin occluders
    and partially covered edges stay conservative.
    """

    __slots__ = ("levels", "width", "height")

    def __init__(self, depths, width, height):
        self.width = width
        self.height = height
        levels = [depths]
        cur, cur_h, cur_w = depths, height, width
        while cur_h > 1 or cur_w > 1:
            nh, nw = (cur_h + 1) // 2, (cur_w + 1) // 2
            nxt = []
            for y in range(nh):
                r0 = cur[2 * y]
                r1 = cur[2 * y + 1] if 2 * y + 1 < cur_h else None
                row = [0.0] * nw
                for x in range(nw):
                    x0 = 2 * x
                    v = r0[x0]
                    if x0 + 1 < cur_w:
                        v = max(v, r0[x0 + 1])
                    if r1 is not None:
                        v = max(v, r1[x0])
                        if x0 + 1 < cur_w:
                            v = max(v, r1[x0 + 1])
                    row[x] = v
                nxt.append(row)
            levels.append(nxt)
            cur, cur_h, cur_w = nxt, nh, nw
        self.levels = levels

    def fully_occluded(self, x0, x1, y0, y1, z):
        """Return ``(covered, visited_nodes)`` for a clipped rectangle.

        Occlusion is strict (``depth < z``) so a tie at equal depth retains the
        instance.  Callers never query an empty footprint: an instance with no
        on-screen projection is dropped by its own visibility test.
        """
        if x0 >= x1 or y0 >= y1:
            return True, 0
        width, height, levels = self.width, self.height, self.levels
        top = len(levels) - 1
        stack = [(top, 0, 0)]
        visited = 0
        while stack:
            level, bx, by = stack.pop()
            visited += 1
            size = 1 << level
            nx0, nx1 = bx * size, min(bx * size + size, width)
            ny0, ny1 = by * size, min(by * size + size, height)
            if levels[level][by][bx] < z:
                continue  # every pixel in this tile is strictly nearer
            # The tile intersects the query; if it is also wholly inside the
            # query its max >= z means some pixel there is not proved occluded
            # (an infinite max means the tile is not covered at all).
            if level == 0 or (nx0 >= x0 and nx1 <= x1 and ny0 >= y0 and ny1 <= y1):
                return False, visited
            half = size >> 1
            for cy in (2 * by, 2 * by + 1):
                cy0 = cy * half
                if cy0 >= y1 or min(cy0 + half, height) <= y0:
                    continue
                for cx in (2 * bx, 2 * bx + 1):
                    cx0 = cx * half
                    if cx0 >= x1 or min(cx0 + half, width) <= x0:
                        continue
                    stack.append((level - 1, cx, cy))
        return True, visited


def _build_depth_buffer(frame, width, height):
    depths = [[INF] * width for _ in range(height)]
    for occ in frame["occluders"]:
        # Transparent primitives never write occlusion depth, same as render().
        if not occ["opaque"]:
            continue
        x0, y0, x1, y1 = occ["rect"]
        z = occ["z"]
        for y in range(max(0, y0), min(y1, height)):
            row = depths[y]
            for x in range(max(0, x0), min(x1, width)):
                if z < row[x]:
                    row[x] = z
    return depths


def _visibility(inst, width, height, near, hierarchy):
    """Conservatively decide whether an instance may draw a pixel.

    Returns ``(id, keep, query_nodes, reason)``; reason is local diagnostics
    only and not part of the output contract.
    """
    box = inst["box"]
    z0, z1 = box[2], box[5]
    if z1 < near:
        return inst["id"], False, 0, "behind-near"
    a, b, c, d = project(box, width, height, near)
    # Outward integer rounding makes the footprint a superset of pixels whose
    # centres lie in the projection; extra pixels can only defeat a cull.
    px0, px1 = max(0, math.floor(a)), min(width, math.ceil(c))
    py0, py1 = max(0, math.floor(b)), min(height, math.ceil(d))
    if px0 >= px1 or py0 >= py1:
        return inst["id"], False, 0, "offscreen"
    if z0 < near:
        # A box crossing the near plane is retained conservatively: the
        # acceptance contract prefers a false keep over a false cull here.
        return inst["id"], True, 0, "crosses-near"
    z = max(near, z0)
    covered, nodes = hierarchy.fully_occluded(px0, px1, py0, py1, z)
    return inst["id"], not covered, nodes, ("occluded" if covered else "visible")


def _frame_signature(frame):
    """Order-independent signature used only to validate a temporal hint."""
    occ = tuple(sorted(
        (tuple(o["rect"]), float(o["z"]), bool(o["opaque"]))
        for o in frame["occluders"]
    ))
    inst = tuple(sorted(
        (i["id"], i["material"], tuple(i["box"]))
        for i in frame["instances"]
    ))
    return occ, inst


def _evaluate(frame, width, height, near, workers, previous):
    """Evaluate a frame and return ``(kept, queries, next_state)``.

    ``previous`` is ``(signature, visible_set, hierarchy)`` from the last frame.
    It never supplies occlusion evidence for a changed frame: any mismatch in
    the order-independent signature -- fast motion, edited occluders, shuffled
    input -- falls back to a freshly built depth hierarchy and a full traversal.
    Only an exactly identical frame may reuse the derived hierarchy, and even
    then every instance is re-queried, so stale visibility can never leak.
    """
    signature = _frame_signature(frame)
    if previous is not None and previous[0] == signature:
        hierarchy, hint = previous[2], previous[1]
    else:
        hierarchy, hint = None, None
    if hierarchy is None:
        hierarchy = DepthHierarchy(
            _build_depth_buffer(frame, width, height), width, height
        )

    instances = frame["instances"]
    if hint:
        order = ([i for i in instances if i["id"] in hint] +
                 [i for i in instances if i["id"] not in hint])
    else:
        order = list(instances)  # fallback: full fresh traversal

    def work(inst):
        return _visibility(inst, width, height, near, hierarchy)

    outcomes = []
    if workers > 1 and len(order) > 1:
        # Fixed contiguous blocks, each evaluated on a worker thread.  Blocks
        # are merged in block order, so neither completion order nor worker
        # count can influence the result.
        block_size = max(1, (len(order) + workers - 1) // workers)
        blocks = [order[i:i + block_size] for i in range(0, len(order), block_size)]

        def process_block(block):
            return [work(inst) for inst in block]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for chunk in pool.map(process_block, blocks):
                outcomes.extend(chunk)
    else:
        outcomes = [work(i) for i in order]

    kept = set()
    queries = 0
    for ident, keep, q, _reason in outcomes:
        if keep:
            kept.add(ident)
        queries += q
    return kept, queries, (signature, frozenset(kept), hierarchy)


def draw(scene, no_cull, workers):
    """Evaluate every frame; output is independent of input order and threads."""
    width, height = scene["size"]
    near = scene["near"]
    if workers < 1:
        workers = 1
    result = []
    previous = None
    for frame in scene["frames"]:
        instances = frame["instances"]
        if no_cull:
            kept = {i["id"] for i in instances}
            queries = 0
            previous = None
        else:
            kept, queries, previous = _evaluate(
                frame, width, height, near, workers, previous
            )
        # Stable batching: materials ascending, ids ascending within each batch.
        # The grouping key is the instance's material string, and identity is
        # carried by the instance id list, so batches never depend on traversal
        # order (including thread completion order).
        batches = {}
        for inst in instances:
            if inst["id"] in kept:
                batches.setdefault(inst["material"], []).append(inst["id"])
        ordered_batches = [
            {"material": material, "ids": sorted(batches[material])}
            for material in sorted(batches)
        ]
        result.append({
            "kept": sorted(kept),
            "batches": ordered_batches,
            "ids": render(kept, instances, frame, width, height, near),
            "stats": {"hierarchy_queries": queries},
        })
    return {"frames": result}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scene", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--no-cull", action="store_true")
    a = p.parse_args()
    data = json.loads(a.scene.read_text())
    result = draw(data, a.no_cull, a.workers)
    a.output.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
