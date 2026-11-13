"""Chunked terrain LOD replay with hysteresis and crack-free seams.

Pipeline per frame:
  1. Level selection per tile from screen error with hysteresis
     (up thresholds 1.2/2.4, down thresholds 0.8/1.8).  Tiles that are
     not ready (interrupted load) stay on their resident ancestor
     coverage, i.e. level 0.  First frame starts every tile from 0.
  2. Adjacency relaxation: neighbours sharing an edge may differ by at
     most one level; the finer end is lowered.  Iterated to the
     (unique, order-independent) greatest fixpoint before meshing.
  3. Meshing: level L -> 2/4/8 segments.  Where a neighbour is coarser,
     the finer tile collapses its shared edge to the coarser vertex set,
     so both sides sample the exact same world positions and the seam
     stays closed.  Each tile keeps exactly one effective coverage
     layer over its whole unit square.
"""
import argparse
import json
from pathlib import Path

UP = (1.2, 2.4)        # error thresholds for 0->1 and 1->2
DOWN = (0.8, 1.8)      # error thresholds for 1->0 and 2->1
SEGMENTS = (2, 4, 8)   # grid segments per level
MAX_LEVEL = 2


def height(x, y):
    """Analytic slope plus sharp ridge along x = 1.35."""
    return 0.15 * x + 0.2 * y + 0.7 * max(0.0, 1.0 - abs(x - 1.35) * 2.0)


def select_level(prev, error, ready):
    """Hysteresis step from the level displayed in the previous frame."""
    if not ready:
        return 0
    level = prev
    if level < 2 and error >= UP[1]:
        level = 2
    elif level < 1 and error >= UP[0]:
        level = 1
    if level == 2 and error < DOWN[1]:
        level = 1
    if level == 1 and error < DOWN[0]:
        level = 0
    return level


def relax_levels(levels, pos):
    """Enforce |level diff| <= 1 on edge neighbours by lowering the finer end."""
    at = {p: tid for tid, p in pos.items()}
    changed = True
    while changed:
        changed = False
        for tid in sorted(levels):
            x, y = pos[tid]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                other = at.get((x + dx, y + dy))
                if other is not None and levels[tid] > levels[other] + 1:
                    levels[tid] = levels[other] + 1
                    changed = True
    return levels


def _boundary(i0, j0, w, h):
    """Grid points on a cell-block boundary, counter-clockwise, no repeats."""
    pts = [(i, j0) for i in range(i0, i0 + w)]
    pts += [(i0 + w, j) for j in range(j0, j0 + h)]
    pts += [(i, j0 + h) for i in range(i0 + w, i0, -1)]
    pts += [(i0, j) for j in range(j0 + h, j0, -1)]
    return pts


def build_tile(x0, y0, level, collapse):
    """Mesh one tile.  collapse = (west, east, south, north) booleans: True
    where the neighbour across that edge is one level coarser, in which case
    this (finer) side collapses its edge to the neighbour's vertex set."""
    n = SEGMENTS[level]
    cw, ce, cs, cn = collapse

    def kept(i, j):
        if cw and i == 0 and j % 2:
            return False
        if ce and i == n and j % 2:
            return False
        if cs and j == 0 and i % 2:
            return False
        if cn and j == n and i % 2:
            return False
        return True

    def coord(i, j):
        x = x0 + i / n
        y = y0 + j / n
        return (x, y, height(x, y))

    faces = []
    handled = set()

    def add_block(i0, j0, w, h, anchor):
        # Fan-triangulate a cell block from `anchor` (a kept grid point:
        # the block centre for 2x2 corners, a collapsed-side endpoint for
        # strips).  Anchoring on the collapsed side avoids degenerate
        # fans through collinear interior-edge points.
        pts = [p for p in _boundary(i0, j0, w, h) if kept(*p)]
        if anchor in pts:                       # boundary anchor: rotate
            k = pts.index(anchor)
            pts = pts[k:] + pts[:k]
            for k in range(1, len(pts) - 1):
                faces.append((anchor, pts[k], pts[k + 1]))
        else:                                   # interior anchor: full fan
            for k in range(len(pts)):
                faces.append((anchor, pts[k], pts[(k + 1) % len(pts)]))
        for i in range(i0, i0 + w):
            for j in range(j0, j0 + h):
                handled.add((i, j))

    # corner 2x2 blocks where two collapsed sides meet
    sw, nw, se, ne = cw and cs, cw and cn, ce and cs, ce and cn
    if sw:
        add_block(0, 0, 2, 2, (1, 1))
    if nw:
        add_block(0, n - 2, 2, 2, (1, n - 1))
    if se:
        add_block(n - 2, 0, 2, 2, (n - 1, 1))
    if ne:
        add_block(n - 2, n - 2, 2, 2, (n - 1, n - 1))
    # edge strips along collapsed sides, in cell pairs, anchored on the
    # collapsed edge so the seam segment matches the coarser neighbour
    if cw:
        for j in range(2 if sw else 0, n - 2 if nw else n, 2):
            add_block(0, j, 1, 2, (0, j))
    if ce:
        for j in range(2 if se else 0, n - 2 if ne else n, 2):
            add_block(n - 1, j, 1, 2, (n, j))
    if cs:
        for i in range(2 if sw else 0, n - 2 if se else n, 2):
            add_block(i, 0, 2, 1, (i, 0))
    if cn:
        for i in range(2 if nw else 0, n - 2 if ne else n, 2):
            add_block(i, n - 1, 2, 1, (i, n))
    # remaining interior cells: standard two-triangle split
    for j in range(n):
        for i in range(n):
            if (i, j) not in handled:
                a, b = (i, j), (i + 1, j)
                c, d = (i + 1, j + 1), (i, j + 1)
                faces.append((a, b, c))
                faces.append((a, c, d))

    # emit only vertices referenced by faces, reindexed
    index = {}
    vertices = []

    def vid(p):
        if p not in index:
            index[p] = len(vertices)
            vertices.append([round(v, 12) for v in coord(*p)])
        return index[p]

    out_faces = [[vid(p) for p in tri] for tri in faces]

    def edge(side):
        if side == "west":
            pts = [(0, j) for j in range(n + 1)]
        elif side == "east":
            pts = [(n, j) for j in range(n + 1)]
        elif side == "south":
            pts = [(i, 0) for i in range(n + 1)]
        else:
            pts = [(i, n) for i in range(n + 1)]
        return [list(coord(*p)) for p in pts if kept(*p)]

    edges = {s: edge(s) for s in ("west", "east", "north", "south")}
    return vertices, out_faces, edges


def replay(data):
    state = {}  # tile id -> level displayed in the previous frame
    frames_out = []
    for frame in data["frames"]:
        tiles = sorted(frame["tiles"], key=lambda t: t["id"])
        pos = {t["id"]: (t["x"], t["y"]) for t in tiles}
        at = {p: tid for tid, p in pos.items()}

        # 1. hysteresis selection; not-ready tiles fall back to ancestor (0)
        levels = {}
        for t in tiles:
            levels[t["id"]] = select_level(state.get(t["id"], 0),
                                           t["error"],
                                           t.get("ready", False))
        # 2. neighbour level-difference constraint, before any meshing
        relax_levels(levels, pos)

        # 3. mesh each tile exactly once: one effective coverage per region
        out_tiles = []
        switches = 0
        for t in tiles:
            tid = t["id"]
            x0, y0 = pos[tid]
            lvl = levels[tid]
            if state.get(tid, 0) != lvl:
                switches += 1
            collapse = []
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nb = at.get((x0 + dx, y0 + dy))
                collapse.append(nb is not None and levels[nb] < lvl)
            vertices, faces, edges = build_tile(x0, y0, lvl, collapse)
            out_tiles.append({
                "id": tid,
                "level": lvl,
                "ready": bool(t.get("ready", False)),
                "bounds": [x0, y0, x0 + 1, y0 + 1],
                "vertices": vertices,
                "faces": faces,
                "edges": edges,
                "triangles": len(faces),
            })
            state[tid] = lvl

        frames_out.append({
            "tiles": out_tiles,
            "triangles": sum(t["triangles"] for t in out_tiles),
            "levels": [t["level"] for t in out_tiles],
            "switches": switches,
        })
    return {"frames": frames_out}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scene", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    a.output.write_text(json.dumps(replay(json.loads(a.scene.read_text())),
                                   sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
