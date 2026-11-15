"""Synthetic HDR inputs for the acceptance tests."""

KEY = 0.18


def exposure_ladder(h=96, w=96, stops=(-10, -6, -3, 0, 3, 6, 9, 12),
                    bands=None, fill=None):
    """Horizontal bands, each a flat patch at a known stop relative to KEY.

    `stops` fixes both the number of bands and the level of each one; `bands`
    defaults to len(stops) and exists only for callers that want to squeeze
    the ladder into fewer bands.  Every row belongs to exactly one band.

    Returns (image, levels) where levels[i] is the linear luma of band i.
    """
    if bands is None:
        bands = len(stops)
    if bands > len(stops):
        raise ValueError("more bands than stops")
    levels = [KEY * 2.0 ** s for s in stops[:bands]]
    img = []
    for r in range(h):
        band = min(bands - 1, r * bands // h)
        img.append([levels[band]] * w)
    return img, levels


def step_edge(h=64, w=64, edge=None, lo=0.02, hi=1.6):
    """Single vertical step edge; ~6.3 EV, black on the left, bright right."""
    edge = w // 2 if edge is None else edge
    return [[hi if c >= edge else lo for c in range(w)] for _ in range(h)], edge


def pure_black_frame(h=32, w=32, eps=0.0):
    return [[eps for _ in range(w)] for _ in range(h)]


def ramp(h=64, w=64, lo_stops=-14.0, hi_stops=6.0):
    """Horizontal geometric ramp from lo_stops to hi_stops around KEY."""
    img = []
    for r in range(h):
        row = []
        for c in range(w):
            s = lo_stops + (hi_stops - lo_stops) * c / max(1, w - 1)
            row.append(KEY * 2.0 ** s)
        img.append(row)
    return img
