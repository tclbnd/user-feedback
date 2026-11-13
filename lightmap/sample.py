"""Sampling and chart inverse mapping.

Two equivalent sampling paths are exposed:

sample_chart  : bilinear sample in a chart's canonical mip chain.
                Because every chain is chart-private and padded inside
                its own grid, the four taps always belong to the same
                chart -- cross-chart bleeding is structurally absent.
sample_atlas  : map (chart id, canonical uv, level) through the
                placement transform and read the atlas; an owner check
                guarantees a foreign texel can never be returned.

inverse_pixel maps an atlas rect pixel back to canonical local coords,
which is what acceptance tests use to invert random packings.
"""
from .atlas import inverse_pixel


def _bilerp(grid, w, fx, fy):
    x0 = int(fx)
    y0 = int(fy)
    x1 = min(w - 1, x0 + 1)
    y1 = min(w - 1, y0 + 1)
    tx = fx - x0
    ty = fy - y0
    v00 = grid[y0 * w + x0]
    v10 = grid[y0 * w + x1]
    v01 = grid[y1 * w + x0]
    v11 = grid[y1 * w + x1]
    top = v00 * (1.0 - tx) + v10 * tx
    bot = v01 * (1.0 - tx) + v11 * tx
    return top * (1.0 - ty) + bot * ty


def sample_chart(chain_entry, u, v):
    """Bilinear sample of one canonical mip image at local uv in [0,1]."""
    w = chain_entry["size"]
    fx = min(max(u, 0.0), 1.0) * (w - 1)
    fy = min(max(v, 0.0), 1.0) * (w - 1)
    return _bilerp(chain_entry["light"], w, fx, fy)


def sample_atlas(atlas_level, placement, chart_id, u, v, rect_size):
    """Sample the atlas through the placement, rejecting foreign owners.

    rect_size is this chart's image size at the requested mip level
    (its rect size in the atlas level grid).
    """
    side = atlas_level["size"]
    n_rect = rect_size
    fx = min(max(u, 0.0), 1.0) * (n_rect - 1)
    fy = min(max(v, 0.0), 1.0) * (n_rect - 1)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(n_rect - 1, x0 + 1), min(n_rect - 1, y0 + 1)
    ox = placement["x"]
    oy = placement["y"]
    rotate = placement.get("rotate", 0)

    taps = []
    for lx, ly, wgt in (
        (x0, y0, (1.0 - (fx - x0)) * (1.0 - (fy - y0))),
        (x1, y0, (fx - x0) * (1.0 - (fy - y0))),
        (x0, y1, (1.0 - (fx - x0)) * (fy - y0)),
        (x1, y1, (fx - x0) * (fy - y0)),
    ):
        rx, ry = _rotate(lx, ly, n_rect, rotate)
        ax, ay = ox + rx, oy + ry
        k = ay * side + ax
        if atlas_level["owners"][k] != chart_id:
            # Foreign or empty texel: skip rather than blend it in.
            continue
        taps.append((atlas_level["light"][k], wgt))
    if not taps:
        return None
    wsum = sum(w for _, w in taps)
    return sum(val * w for val, w in taps) / wsum


def _rotate(x, y, n, rotate):
    from .atlas import rotate_pixel
    return rotate_pixel(x, y, n, rotate)


def invert_atlas_texel(chart_id, ax, ay, placement, level_size):
    """Atlas texel -> canonical local (x,y) for a placed chart, or None."""
    lx = ax - placement["x"]
    ly = ay - placement["y"]
    if not (0 <= lx < level_size and 0 <= ly < level_size):
        return None
    cx, cy = inverse_pixel(lx, ly, level_size, placement.get("rotate", 0))
    return cx, cy
