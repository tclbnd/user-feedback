"""Coverage masks: tiled scanliner plus an independent winding reference.

Both implementations consume the same flattened segments and the same sample
coordinates (see rsample.geometry). The production rasterizer sweeps sorted
crossings along each sample row; the reference casts a ray independently for
every sample from scratch. They must agree bit for bit.

A mask is a per-pixel 16-bit integer for the fixed 4x4 sample grid; bit
4*i + j is set when sample (i, j) is covered. Tiles only partition the work:
every sample is evaluated from global coordinates, so changing the tile size
cannot change a single bit.
"""

import math
from bisect import bisect_right

from .geometry import crossing_winding, flatten_path

SAMPLES = 4


def _apply_rule(winding, rule):
    if rule == "nonzero":
        return winding != 0
    if rule == "evenodd":
        return (winding & 1) != 0  # parity of a negative integer still works
    raise ValueError("unknown fill rule: " + repr(rule))


def _ceil(v):
    return math.ceil(v - 1e-12)


def _row_buckets(segments, n):
    """Group segments by the global sample rows their lower/upper range hits."""
    buckets = {}
    for seg in segments:
        ylo = min(seg[1], seg[3])
        yhi = max(seg[1], seg[3])
        # lo <= (r + 0.5)/n < hi  <=>  n*lo - 0.5 <= r < n*hi - 0.5
        r_lo = _ceil(n * ylo - 0.5)
        r_hi = _ceil(n * yhi - 0.5)
        for r in range(r_lo, r_hi):
            buckets.setdefault(r, []).append(seg)
    return buckets


def _sweep_row(segments, sy, i, width, n, rule):
    """Coverage bits for sample row i of one pixel row, across all columns.

    i is the sample row within the pixel (0..n-1); the returned word uses bit
    n*i + j. Both engines cast a ray to +x: the winding at a sample is the sum
    of every crossing strictly RIGHT of the sample x. Crossings exactly on the
    sample are excluded (strict greater-than), so the sweep and the independent
    per-sample cast agree even on edges that pass through a sample point.
    """
    crossings = []
    for seg in segments:
        hit = crossing_winding(seg, sy)
        if hit is not None:
            crossings.append((hit[1], hit[0]))
    crossings.sort()
    xs = [c[0] for c in crossings]
    # Suffix winding: suffix[k] is the summed contribution of crossings k..end,
    # i.e. all crossings with x >= xs[k]. bisect_right gives the first index
    # whose x is strictly greater than sx, so equal-x ties are never counted.
    total = sum(sign for _, sign in crossings)
    suffix = [total]
    running = total
    for _, sign in crossings:
        running -= sign
        suffix.append(running)

    bits = [0] * width
    for x in range(width):
        word = 0
        for j in range(n):
            sx = x + (j + 0.5) / n
            k = bisect_right(xs, sx)
            if _apply_rule(suffix[k], rule):
                word |= 1 << (n * i + j)
        bits[x] = word
    return bits

def rasterize_tiled(segments, width, height, rule, tile=8, n=SAMPLES):
    """Production rasterizer. Tiles are half-open [x0,x1) x [y0,y1).

    The tile rectangle only selects which output pixels are filled; every
    crossing and sample coordinate is computed in the global frame, so tile
    size (including a tile larger than the image, i.e. partial off-screen)
    yields identical masks.
    """
    mask = [[0] * width for _ in range(height)]
    if not segments:
        return mask

    for y0 in range(0, height, tile):
        y1 = min(y0 + tile, height)
        # Only segments whose y range reaches this band can contribute.
        band = [s for s in segments
                if min(s[1], s[3]) < y1 and max(s[1], s[3]) > y0]
        rows = _row_buckets(band, n)
        for y in range(y0, y1):
            for i in range(n):
                r = y * n + i
                sy = y + (i + 0.5) / n
                row_bits = _sweep_row(rows.get(r, ()), sy, i, width, n, rule)
                for x, word in enumerate(row_bits):
                    mask[y][x] |= word
    return mask


def rasterize_reference(segments, width, height, rule, n=SAMPLES):
    """Independent per-sample winding number, no shared scanline state.

    Each sample re-casts its +x ray against every segment. This deliberately
    duplicates the sweep logic differently so the two implementations can
    cross-check tie handling, tangencies and self-intersection.
    """
    mask = [[0] * width for _ in range(height)]
    for y in range(height):
        for i in range(n):
            sy = y + (i + 0.5) / n
            for x in range(width):
                word = 0  # fresh per pixel; accumulated into the row below
                for j in range(n):
                    sx = x + (j + 0.5) / n
                    winding = 0
                    for seg in segments:
                        hit = crossing_winding(seg, sy)
                        if hit is not None and hit[1] > sx:
                            winding += hit[0]
                    if _apply_rule(winding, rule):
                        word |= 1 << (n * i + j)
                mask[y][x] |= word
    return mask


def popcount(word):
    return bin(word).count("1")


def path_mask(path, width, height, ox, oy, rasterizer, tile=8, n=SAMPLES):
    segments = flatten_path(path["commands"], ox, oy)
    rule = path.get("rule", "nonzero")
    if rasterizer is rasterize_tiled:
        return rasterizer(segments, width, height, rule, tile, n)
    return rasterizer(segments, width, height, rule, n)


def combine_layers(shape_mask, clip_masks):
    """Intersect every layer on the identical per-sample bits.

    A transparent sample (zero bit in any clip) stays transparent in the
    combined mask, which keeps premultiplied compositing consistent: coverage
    is the product of the per-layer indicator functions evaluated at the same
    sample point.
    """
    combined = [row[:] for row in shape_mask]
    for clip in clip_masks:
        for y, row in enumerate(combined):
            other = clip[y]
            combined[y] = [v & other[x] for x, v in enumerate(row)]
    return combined


def composite(mask, color, n=SAMPLES):
    """Premultiplied linear RGBA: each channel scales by covered fraction."""
    scale = 1.0 / (n * n)
    rgba = []
    for row in mask:
        out_row = []
        for word in row:
            k = popcount(word)
            if k == 0:
                out_row.append([0.0, 0.0, 0.0, 0.0])
            else:
                f = k * scale
                out_row.append([round(c * f, 9) for c in color])
        rgba.append(out_row)
    return rgba


def render(scene, tile=8, rasterizer=rasterize_tiled, n=SAMPLES):
    """Render a scene dict with mask and rgba keys.

    rasterizer switches the production scanliner for the independent winding
    reference; both share the path flattening and sample convention.
    """
    width, height = scene["size"]
    ox, oy = scene.get("origin", [0, 0])
    ox, oy = float(ox), float(oy)

    shape_mask = path_mask(scene["shape"], width, height, ox, oy,
                           rasterizer, tile, n)
    clip_masks = [
        path_mask(clip, width, height, ox, oy, rasterizer, tile, n)
        for clip in scene.get("clips", [])
    ]
    final_mask = combine_layers(shape_mask, clip_masks)
    rgba = composite(final_mask, scene["color"], n)
    return dict(mask=final_mask, rgba=rgba)
