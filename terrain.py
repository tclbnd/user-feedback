"""Chunked terrain LOD replay: hysteresis level selection, restricted
neighbour levels and crack-free stitched meshes.

Contract (see README.md):  python3 terrain.py demo.json output.json

Rules implemented here:
  * Level selection is driven by the per-tile screen error with hysteresis:
    raise at 1.2 / 2.4, lower at 0.8 / 1.8, always starting from the
    previous frame's effective level.  A tile that is not ready (loading
    interrupted / not yet streamed in) falls back to its resident level-0
    ancestor coverage, so every tile has exactly one coverage source per
    frame.
  * Neighbouring tiles may differ by at most one level.  The constraint is
    enforced by lowering the finer side, iterated to a fixpoint that is
    independent of input tile order.  Meshes are built only afterwards.
  * Shared edges use exactly the same set of world points: the finer side
    collapses its boundary to the coarser spacing and the seam indices are
    rebuilt (strip fans along the edge, corner fans where two stitched
    edges meet).  Both sides therefore sample identical world positions,
    including while levels are in transition.
"""
import argparse
import json
from pathlib import Path

UP = (1.2, 2.4)      # error thresholds to raise level 0->1 and 1->2
DOWN = (0.8, 1.8)    # error thresholds to lower level 1->0 and 2->1
MAX_LEVEL = 2

EDGES = ("west", "east", "south", "north")
NEIGHBOUR_OFFSET = {"west": (-1, 0), "east": (1, 0),
                    "south": (0, -1), "north": (0, 1)}
OPPOSITE = {"west": "east", "east": "west", "south": "north", "north": "south"}


def height(x, y):
    """Analytic height field: planar slope plus a sharp tent ridge."""
    return 0.15 * x + 0.2 * y + 0.7 * max(0.0, 1.0 - abs(x - 1.35) * 2.0)


def step_level(prev, error, up=UP, down=DOWN):
    """One hysteresis step from the previous effective level."""
    lv = prev
    while lv < MAX_LEVEL and error >= up[lv]:
        lv += 1
    while lv > 0 and error < down[lv - 1]:
        lv -= 1
    return lv


def grid_segments(level):
    """Level 0/1/2 tessellates the tile into 2/4/8 segments per side."""
    return 1 << (level + 1)


# ---------------------------------------------------------------- meshes

def _corner_fan(faces, vid, ci, cj, c_south, c_east, c_north, c_west):
    """Triangulate the 2x2 cell block at (ci,cj) as a fan around its centre.

    Boundary mid-vertices on collapsed edges are dropped so the block boundary
    stays on the shared (coarser) world points."""
    poly = [(ci, cj)]
    if not c_south:
        poly.append((ci + 1, cj))
    poly.append((ci + 2, cj))
    if not c_east:
        poly.append((ci + 2, cj + 1))
    poly.append((ci + 2, cj + 2))
    if not c_north:
        poly.append((ci + 1, cj + 2))
    poly.append((ci, cj + 2))
    if not c_west:
        poly.append((ci, cj + 1))
    centre = vid(ci + 1, cj + 1)
    ring = [vid(i, j) for i, j in poly]
    for k in range(len(ring)):
        faces.append([centre, ring[k], ring[(k + 1) % len(ring)]])


def _strip(faces, outer, inner):
    """Triangulate one cell-row between the (possibly collapsed) outer edge
    polyline and the full-resolution inner polyline."""
    if len(outer) == len(inner):
        for k in range(len(outer) - 1):
            o0, o1 = outer[k], outer[k + 1]
            i0, i1 = inner[k], inner[k + 1]
            faces.append([o0, o1, i1])
            faces.append([o0, i1, i0])
    else:  # outer collapsed to every second vertex: rebuild seam indices
        for k in range(len(outer) - 1):
            o0, o1 = outer[k], outer[k + 1]
            i0, i1, i2 = inner[2 * k], inner[2 * k + 1], inner[2 * k + 2]
            faces.append([o0, o1, i1])
            faces.append([o0, i1, i0])
            faces.append([o1, i2, i1])


def _orient(faces, n):
    """Make every triangle counter-clockwise in the XY plane."""
    out = []
    for a, b, c in faces:
        ax, ay = a % (n + 1), a // (n + 1)
        bx, by = b % (n + 1), b // (n + 1)
        cx, cy = c % (n + 1), c // (n + 1)
        cross = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        out.append([a, b, c] if cross > 0 else [a, c, b])
    return out


def build_faces(n, collapsed):
    """Rebuild the tile triangulation; edges whose neighbour is coarser are
    collapsed to the neighbour's spacing and stitched to the interior."""
    faces = []

    def vid(i, j):
        return j * (n + 1) + i

    if n == 2:  # the whole tile is a single 2x2 block
        _corner_fan(faces, vid, 0, 0, collapsed["south"], collapsed["east"],
                    collapsed["north"], collapsed["west"])
        return _orient(faces, n)

    skip = {(1, 1), (1, n - 2), (n - 2, 1), (n - 2, n - 2)}
    for i in range(1, n - 1):
        for j in range(1, n - 1):
            if (i, j) in skip:
                continue
            a, b = vid(i, j), vid(i + 1, j)
            c, d = vid(i + 1, j + 1), vid(i, j + 1)
            faces.append([a, b, c])
            faces.append([a, c, d])

    rng = list(range(2, n - 1))
    even = list(range(2, n - 1, 2))
    _strip(faces, [vid(i, 0) for i in (even if collapsed["south"] else rng)],
           [vid(i, 1) for i in rng])
    _strip(faces, [vid(i, n) for i in (even if collapsed["north"] else rng)],
           [vid(i, n - 1) for i in rng])
    _strip(faces, [vid(0, j) for j in (even if collapsed["west"] else rng)],
           [vid(1, j) for j in rng])
    _strip(faces, [vid(n, j) for j in (even if collapsed["east"] else rng)],
           [vid(n - 1, j) for j in rng])

    _corner_fan(faces, vid, 0, 0, collapsed["south"], False, False,
                collapsed["west"])
    _corner_fan(faces, vid, n - 2, 0, collapsed["south"], collapsed["east"],
                False, False)
    _corner_fan(faces, vid, 0, n - 2, False, False, collapsed["north"],
                collapsed["west"])
    _corner_fan(faces, vid, n - 2, n - 2, False, collapsed["east"],
                collapsed["north"], False)
    return _orient(faces, n)


def build_vertices(x, y, n):
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            px = x + i / n
            py = y + j / n
            verts.append([px, py, height(px, py)])
    return verts


def _edge_points(x, y, n, collapsed, edge):
    """Effective boundary world points of one edge, ascending in the other
    axis.  Collapsed edges list exactly the shared (coarser) points."""
    idx = range(0, n + 1, 2) if collapsed else range(0, n + 1)
    pts = []
    for k in idx:
        if edge == "west":
            px, py = x, y + k / n
        elif edge == "east":
            px, py = x + 1, y + k / n
        elif edge == "south":
            px, py = x + k / n, y
        else:
            px, py = x + k / n, y + 1
        pts.append([px, py, height(px, py)])
    return pts


def build_tile(t, level, neighbour_levels):
    x, y = t["x"], t["y"]
    n = grid_segments(level)
    collapsed = {}
    for e in EDGES:
        nl = neighbour_levels.get(e)
        collapsed[e] = nl is not None and nl < level
    faces = build_faces(n, collapsed)
    return {
        "id": t["id"],
        "level": level,
        "desired_level": level,  # overwritten by replay() with pre-constraint
        "bounds": [x, y, x + 1, y + 1],
        "vertices": build_vertices(x, y, n),
        "faces": faces,
        "edges": {e: _edge_points(x, y, n, collapsed[e], e) for e in EDGES},
        "triangles": len(faces),
    }


# ---------------------------------------------------------------- replay

def replay(data, up=UP, down=DOWN):
    prev = {}
    out_frames = []
    for frame in data["frames"]:
        tiles = sorted(frame["tiles"], key=lambda t: t["id"])
        at = {(t["x"], t["y"]): t["id"] for t in tiles}

        # 1) hysteresis level selection; not-ready tiles keep only their
        #    resident level-0 ancestor coverage (single coverage source).
        desired = {}
        for t in tiles:
            lv = step_level(prev.get(t["id"], 0), t["error"], up, down)
            if not t.get("ready", False):
                lv = 0
            desired[t["id"]] = lv

        # 2) restrict neighbour level difference to <= 1 (lower the finer
        #    side), iterated to an order-independent fixpoint.
        level = dict(desired)
        changed = True
        while changed:
            changed = False
            for t in tiles:
                i = t["id"]
                for dx, dy in NEIGHBOUR_OFFSET.values():
                    j = at.get((t["x"] + dx, t["y"] + dy))
                    if j is not None and level[i] > level[j] + 1:
                        level[i] = level[j] + 1
                        changed = True

        # 3) meshes with rebuilt seam indices, after levels are final.
        out_tiles = []
        switches = 0
        for t in tiles:
            i = t["id"]
            nl = {}
            for e, (dx, dy) in NEIGHBOUR_OFFSET.items():
                j = at.get((t["x"] + dx, t["y"] + dy))
                if j is not None:
                    nl[e] = level[j]
            tile_out = build_tile(t, level[i], nl)
            tile_out["desired_level"] = desired[i]
            out_tiles.append(tile_out)
            if prev.get(i, 0) != level[i]:
                switches += 1
        prev = level
        out_frames.append({
            "tiles": out_tiles,
            "triangles": sum(t["triangles"] for t in out_tiles),
            "switches": switches,
        })
    return {"frames": out_frames}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scene", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    out = replay(json.loads(a.scene.read_text()))
    a.output.write_text(json.dumps(out, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
