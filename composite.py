"""Bounded-memory, order-independent multisample transparent compositing.

Pipeline (all blending happens in linear premultiplied space):

  1. Per-sample occlusion: a transparent fragment whose z is greater than or
     equal to the opaque surface z of *that sample* is culled.  The test is
     per sample, so a transparent object may cross an opaque surface (visible
     on samples where it is in front, occluded on samples where it is behind).
  2. Stable ordering: inside one sample ray, fragments are ordered near to far
     by the key (z, id).  ``id`` is an intrinsic, submission-independent global
     integer, so fragments sharing a depth get a deterministic order no matter
     how the draw calls were submitted.
  3. Bounded path: tiles are processed one at a time.  Fragments of samples
     whose valid count fits in ``budget`` are cached and sorted; cache batches
     are capped so the number of simultaneously resident fragment records
     never exceeds the budget.
  4. Accurate fallback: a sample whose valid fragment count exceeds the budget
     is never truncated.  It is composited with an exact multi-pass scan over
     the read-only source (rank-k selection on key (z, id)); at most one
     record is resident at a time.  Affected pixels/regions are reported.
  5. MSAA resolve averages per-sample linear RGBA; display encoding (gamma)
     is a separate post-resolve step.

Zero-alpha fragments participate in neither compositing nor capacity
accounting; alpha == 1 fragments correctly occlude everything behind them;
very small (but non-zero) alpha is never culled.
"""

import argparse
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared small helpers
# ---------------------------------------------------------------------------

def valid_fragment(frag, opaque_sample):
    """Return False for zero-alpha or occluded fragments.

    Opaque z is looked up per sample, which is what lets a transparent object
    intersect / cross an opaque surface.
    """
    if float(frag["rgba"][3]) <= 0.0:
        return False
    oz = opaque_sample.get("z")
    if oz is not None and float(frag["z"]) >= float(oz):
        return False
    return True


def over_front_to_back(acc, src):
    """Premultiplied OVER accumulated front-to-back.

    acc holds straight-alpha accumulator state (rgb, a); src is premultiplied.
    An already-opaque accumulator is unchanged, which is the front-to-back
    form of occlusion by an alpha == 1 fragment.
    """
    _, aa = acc
    w = 1.0 - aa
    return (
        (acc[0][0] + src[0] * w,
         acc[0][1] + src[1] * w,
         acc[0][2] + src[2] * w),
        aa + src[3] * w,
    )


def encode_gamma(rgba, gamma):
    """Display encoding applied *after* the linear MSAA resolve."""
    inv = 1.0 / gamma
    return [
        rgba[0] ** inv,
        rgba[1] ** inv,
        rgba[2] ** inv,
        rgba[3],  # coverage / alpha is not display-encoded
    ]


# ---------------------------------------------------------------------------
# Main bounded-memory pipeline
# ---------------------------------------------------------------------------

def render(scene, tile, budget, gamma=None):
    if not isinstance(tile, int) or tile <= 0:
        raise ValueError("tile must be a positive integer")
    if not isinstance(budget, int) or budget <= 0:
        raise ValueError("budget must be a positive integer")

    width, height = scene["size"]
    n_samples = scene["samples"]
    opaque = scene["opaque"]
    fragments = scene["fragments"]

    # out_rgba[y][x] is lazily allocated as a per-sample list.
    out_rgba = [[None] * width for _ in range(height)]
    stats = {
        "overflow_pixels": 0,
        "peak_fragment_records": 0,
        "fallback_invocations": 0,
        "fallback_region": None,        # bounding box of fallback pixels
        "fallback_pixels": [],          # explicit affected pixel list
        "zero_alpha_fragments": 0,
        "occluded_fragments": 0,
        "valid_fragments": 0,
        "tiles_processed": 0,
        "source_scans": 0,              # full passes over the read-only source
    }
    peak = 0
    fb_min_x, fb_min_y = width, height
    fb_max_x, fb_max_y = -1, -1

    for ty0 in range(0, height, tile):
        for tx0 in range(0, width, tile):
            tx1 = min(width, tx0 + tile)
            ty1 = min(height, ty0 + tile)

            # groups[(x, y, s)] = list of source fragment indices.
            # Index/stat bookkeeping is lightweight and not counted against
            # the fragment-record budget (see README).
            groups = {}
            for idx, frag in enumerate(fragments):
                x, y, s = frag["x"], frag["y"], frag["sample"]
                if not (tx0 <= x < tx1 and ty0 <= y < ty1):
                    continue
                op = opaque[y][x][s]
                if float(frag["rgba"][3]) <= 0.0:
                    stats["zero_alpha_fragments"] += 1
                    continue
                oz = op.get("z")
                if oz is not None and float(frag["z"]) >= float(oz):
                    stats["occluded_fragments"] += 1
                    continue
                stats["valid_fragments"] += 1
                groups.setdefault((x, y, s), []).append(idx)
            stats["source_scans"] += 1  # this tile's gather pass

            # The largest set of records resident simultaneously is bounded by
            # the per-sample overflow threshold and by the explicit cache
            # batch cap below.
            peak = max(peak,
                       min(budget,
                           max((len(v) for v in groups.values()), default=0)))

            # Samples within budget use the cached, sorted path; over-capacity
            # samples are deferred to the accurate fallback.
            normal, overflow = [], []
            for key, ids in groups.items():
                (overflow if len(ids) > budget else normal).append((key, ids))

            # ---- bounded cached path: batches never exceed the budget ----
            cache = []
            for key, ids in normal:
                if len(cache) + len(ids) > budget:
                    peak = max(peak, len(cache))
                    cache = []
                cache.extend(ids)
                peak = max(peak, len(cache))
                _composite_cached_sample(key, fragments, opaque,
                                         out_rgba, n_samples, ids)
            peak = max(peak, len(cache))

            # ---- exact fallback: repeated source scans, 1 record resident --
            for key, _ids in overflow:
                stats["fallback_invocations"] += 1
                scans = _composite_fallback_sample(key, fragments, opaque,
                                                   out_rgba, n_samples)
                stats["source_scans"] += scans
                peak = max(peak, 1)  # a rank scan holds one record
                x, y, _ = key
                stats["fallback_pixels"].append([x, y])
                fb_min_x = min(fb_min_x, x)
                fb_min_y = min(fb_min_y, y)
                fb_max_x = max(fb_max_x, x)
                fb_max_y = max(fb_max_y, y)

            stats["tiles_processed"] += 1

    # Resolve every pixel: samples without a valid fragment keep that sample's
    # opaque background.
    fallback_pixel_set = {(p[0], p[1]) for p in stats["fallback_pixels"]}
    stats["overflow_pixels"] = len(fallback_pixel_set)
    for y in range(height):
        for x in range(width):
            means = [0.0, 0.0, 0.0, 0.0]
            stored = out_rgba[y][x]
            for s in range(n_samples):
                rgba = (stored[s] if stored is not None and stored[s] is not None
                        else opaque[y][x][s]["rgba"])
                for c in range(4):
                    means[c] += rgba[c]
            means = [v / n_samples for v in means]
            if gamma is not None:
                means = encode_gamma(means, gamma)
            out_rgba[y][x] = means

    if stats["fallback_pixels"]:
        stats["fallback_region"] = {
            "x0": fb_min_x, "y0": fb_min_y,
            "x1": fb_max_x, "y1": fb_max_y,
        }
    stats["peak_fragment_records"] = peak
    return {"rgba": out_rgba, "stats": stats}


def _composite_cached_sample(key, fragments, opaque, out_rgba, n_samples, ids):
    """Sort the cached records by (z, id) and accumulate front-to-back."""
    x, y, s = key
    ordered = sorted(ids,
                     key=lambda i: (float(fragments[i]["z"]),
                                    fragments[i]["id"]))
    final = _analytic_composite(ordered, fragments,
                                opaque[y][x][s]["rgba"])
    if out_rgba[y][x] is None:
        out_rgba[y][x] = [None] * n_samples
    out_rgba[y][x][s] = final


def _composite_fallback_sample(key, fragments, opaque, out_rgba, n_samples):
    """Exact compositing for an over-budget sample without truncation.

    Rank-k selection: each scan over the read-only source extracts the k-th
    smallest record by key (z, id), keeping only one fragment record resident
    at a time.  Every valid record is reproduced in exact sorted order, so the
    result uses the same analytic OVER math as the cached path.
    """
    x, y, s = key
    op = opaque[y][x][s]
    ids = [i for i, f in enumerate(fragments)
           if f["x"] == x and f["y"] == y and f["sample"] == s
           and valid_fragment(f, op)]
    scans = 1  # the id-collecting source scan above
    remaining = set(ids)
    ordered = []
    while remaining:
        best = None  # exactly one resident record during the scan
        for i in remaining:
            key_i = (float(fragments[i]["z"]), fragments[i]["id"])
            if best is None or key_i < best[0]:
                best = (key_i, i)
        chosen = best[1]
        remaining.discard(chosen)
        ordered.append(chosen)
        scans += 1
        if float(fragments[chosen]["rgba"][3]) >= 1.0:
            # Opaque record: everything still farther away is occluded.
            break
    final = _analytic_composite(ordered, fragments, op["rgba"])
    if out_rgba[y][x] is None:
        out_rgba[y][x] = [None] * n_samples
    out_rgba[y][x][s] = final
    return scans


def _analytic_composite(ordered_indices, fragments, base_rgba):
    """Front-to-back premultiplied OVER, then composite over the base."""
    acc = ((0.0, 0.0, 0.0), 0.0)
    for i in ordered_indices:
        src = fragments[i]["rgba"]
        acc = over_front_to_back(acc, src)
        if acc[1] >= 1.0:  # opaque fragment blocks everything farther away
            break
    (r, g, b), a = acc
    w = 1.0 - a
    return [r + base_rgba[0] * w,
            g + base_rgba[1] * w,
            b + base_rgba[2] * w,
            a + base_rgba[3] * w]


# ---------------------------------------------------------------------------
# Independent reference: per-ray sort + analytic back-to-front OVER.
#
# Deliberately written without tiles, budget or any shared accumulation code,
# so it acts as an independent oracle for acceptance comparisons.
# ---------------------------------------------------------------------------

def render_reference(scene, gamma=None):
    width, height = scene["size"]
    n_samples = scene["samples"]
    opaque = scene["opaque"]
    rays = {}
    for frag in scene["fragments"]:
        x, y, s = frag["x"], frag["y"], frag["sample"]
        if not valid_fragment(frag, opaque[y][x][s]):
            continue
        rays.setdefault((x, y, s), []).append(frag)

    out = [[None] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            per_sample = []
            for s in range(n_samples):
                acc = list(opaque[y][x][s]["rgba"])
                ray = rays.get((x, y, s), [])
                # Back-to-front premultiplied OVER: src over dst.
                for f in sorted(ray,
                                key=lambda fr: (float(fr["z"]), fr["id"]),
                                reverse=True):
                    sr, sg, sb, sa = f["rgba"]
                    inv = 1.0 - sa
                    acc = [sr + acc[0] * inv,
                           sg + acc[1] * inv,
                           sb + acc[2] * inv,
                           sa + acc[3] * inv]
                per_sample.append(acc)
            means = [sum(per_sample[s][c] for s in range(n_samples))
                     / n_samples for c in range(4)]
            if gamma is not None:
                means = encode_gamma(means, gamma)
            out[y][x] = means
    return {"rgba": out}


def main():
    p = argparse.ArgumentParser(
        description="Bounded-memory order-independent transparency compositor")
    p.add_argument("scene", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--tile", type=int, default=8)
    p.add_argument("--budget", type=int, default=64)
    p.add_argument("--gamma", type=float, default=None,
                   help="optional display gamma applied after linear resolve")
    p.add_argument("--reference", action="store_true",
                   help="run the independent unbounded reference renderer")
    a = p.parse_args()

    scene = json.loads(a.scene.read_text())
    if a.reference:
        result = render_reference(scene, gamma=a.gamma)
    else:
        result = render(scene, a.tile, a.budget, gamma=a.gamma)
    a.output.write_text(
        json.dumps(result, allow_nan=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
