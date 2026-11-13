"""Deterministic chart packing with 90-degree rotation support.

Two strategies are provided:

pack_shelf  : deterministic next-fit decreasing-height shelf layout.
pack_random : seeded random permutation, random rotation and random
              shelf jitter.  The RNG is an internal LCG, so a seed
              always reproduces the exact layout; packing never changes
              a chart's canonical mip chain.

A placement is dict(chart_id, x, y, w, h, rotate).  rotate is the
clockwise rotation applied when blitting the canonical grid into the
atlas, matching the README convention.
"""


class LCG:
    """Deterministic, dependency-free RNG (Numerical Recipes constants)."""

    def __init__(self, seed):
        self.state = (int(seed) & 0xFFFFFFFF) or 0x9E3779B9

    def next_u32(self):
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state

    def randrange(self, stop):
        return self.next_u32() % max(1, int(stop))

    def shuffle(self, seq):
        for i in range(len(seq) - 1, 0, -1):
            j = self.randrange(i + 1)
            seq[i], seq[j] = seq[j], seq[i]
        return seq


def rotated_dims(size, rotate):
    """Canonical (w,h) after a clockwise rotation (charts are square)."""
    return (size, size)


def pack_shelf(chart_sizes, gap=0):
    """Pack chart ids (id -> size) onto square atlases, shelf by shelf."""
    items = sorted(chart_sizes.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(s * s for _, s in items)
    side = 1
    while side * side < total:
        side *= 2
    placements = {}
    while True:
        shelf_y = 0
        shelf_h = 0
        cursor = 0
        ok = True
        placements = {}
        for cid, size in items:
            w = h = size
            if cursor + w <= side and shelf_y + h <= side:
                pass
            elif shelf_y + shelf_h + h <= side and w <= side:
                shelf_y += shelf_h
                shelf_h = 0
                cursor = 0
            else:
                ok = False
                break
            placements[cid] = {"chart_id": cid, "x": cursor, "y": shelf_y,
                               "w": w, "h": h, "rotate": 0}
            cursor += w + gap
            shelf_h = max(shelf_h, h)
        if ok:
            return side, placements
        side *= 2


def pack_random(chart_sizes, seed, gap=0):
    """Seeded random packing; deterministic for a given seed."""
    rng = LCG(seed)
    items = rng.shuffle(list(chart_sizes.items()))
    side = 1
    total = sum(s * s for _, s in items)
    while side * side < total:
        side *= 2
    while True:
        placements = {}
        occupied = []
        ok = True
        for cid, size in items:
            rotate = rng.randrange(4) * 90
            w = h = size  # square charts: rotation keeps the footprint
            candidates = []
            for y in range(0, side - h + 1, max(1, size // 2) if size > 1 else 1):
                for x in range(0, side - w + 1, max(1, size // 2) if size > 1 else 1):
                    x_right, y_bottom = x + w, y + h
                    if all(x_right <= o[0] or o[2] <= x
                           or y_bottom <= o[1] or o[3] <= y
                           for o in occupied):
                        candidates.append((x, y))
            if not candidates:
                ok = False
                break
            x, y = candidates[rng.randrange(len(candidates))]
            placements[cid] = {"chart_id": cid, "x": x, "y": y,
                               "w": w, "h": h, "rotate": rotate}
            occupied.append((x, y, x + w, y + h))
        if ok:
            return side, placements
        side *= 2
