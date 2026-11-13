"""Atlas assembly with explicit, traceable collision handling.

The atlas never averages texels across charts.  Each level is assembled
by blitting per-chart canonical mip images through the (origin, rotate)
transform.  If two charts ever land on the same atlas texel -- this can
only happen at coarse mip levels where distinct rectangles shrink onto
one cell -- the winner is chosen deterministically (smallest chart id)
and the loser is recorded in level["conflicts"] instead of being blended
in.  No cross-chart filter colour is ever produced.
"""


def rotate_pixel(x, y, n, rotate):
    """Canonical local pixel -> rotated local pixel (clockwise degrees)."""
    if rotate == 90:
        return (n - 1 - y, x)
    if rotate == 180:
        return (n - 1 - x, n - 1 - y)
    if rotate == 270:
        return (y, n - 1 - x)
    return (x, y)


def inverse_pixel(ax, ay, n, rotate):
    """Atlas-rect pixel -> canonical local pixel (inverse rotation)."""
    if rotate == 90:
        # (ax,ay) = (N-1-y, x)  =>  (x,y) = (ay, N-1-ax)
        return (ay, n - 1 - ax)
    if rotate == 180:
        return (n - 1 - ax, n - 1 - ay)
    if rotate == 270:
        # (ax,ay) = (y, N-1-x)  =>  (x,y) = (N-1-ay, ax)
        return (n - 1 - ay, ax)
    return (ax, ay)


def atlas_size_for(placements, chart_sizes, level=0):
    """Smallest power-of-two side covering every placement at a level."""
    need = 0
    for cid, p in placements.items():
        s = max(1, chart_sizes[cid] >> level)
        need = max(need, (p["x"] >> level) + s, (p["y"] >> level) + s)
    side = 1
    while side < need:
        side *= 2
    return side


def blit_level(placements, chart_sizes, chains, level, atlas_side=None):
    """Blit one mip level into an atlas grid without cross-chart mixing.

    Returns dict(side, light, owner, chart_at, conflicts) where
    chart_at maps a flat atlas index to the winning chart id and
    conflicts lists every rejected texel with both claimants.
    """
    side = atlas_side or atlas_size_for(placements, chart_sizes, level)
    total = side * side
    light = [None] * total
    owner = [None] * total
    conflicts = []
    # Deterministic write order: ascending chart id.  Reordering the
    # actual writes cannot change the final image because the winner is
    # picked by id, not by write timing.
    for cid in sorted(placements, key=lambda c: str(c)):
        p = placements[cid]
        if level >= len(chains[cid]):
            continue
        entry = chains[cid][level]
        n = entry["size"]
        ox = p["x"] >> level
        oy = p["y"] >> level
        rotate = p["rotate"]
        for ly in range(n):
            for lx in range(n):
                rx, ry = rotate_pixel(lx, ly, n, rotate)
                ax, ay = ox + rx, oy + ry
                if not (0 <= ax < side and 0 <= ay < side):
                    conflicts.append({
                        "level": level, "chart": cid, "atlas": [ax, ay],
                        "reason": "out_of_bounds",
                    })
                    continue
                k = ay * side + ax
                src = ly * n + lx
                if entry["owner"][src] != cid:
                    continue
                if owner[k] is None:
                    owner[k] = cid
                    light[k] = entry["light"][src]
                elif owner[k] != cid:
                    # Collision at this coarse cell: keep a deterministic
                    # winner, never average, fully record the loser.
                    winner = min(str(owner[k]), str(cid))
                    conflicts.append({
                        "level": level, "atlas": [ax, ay],
                        "winner": winner,
                        "rejected": cid if winner == str(owner[k]) else owner[k],
                        "rejected_light": entry["light"][src],
                    })
                    if str(cid) == winner:
                        owner[k] = cid
                        light[k] = entry["light"][src]
    return {
        "side": side,
        "light": light,
        "owner": owner,
        "conflicts": conflicts,
        "policy": "deterministic_min_chart_id; no cross-chart averaging",
    }
