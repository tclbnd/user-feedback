#!/usr/bin/env python3
"""Adaptive block-boundary (deblocking) post filter.

Usage: python3 deblock.py input.json output.json

The filter is pure: the parsed fixture is never modified and the returned
frame is a fresh grid, so callers can keep using their input.

See README.md for the algorithm and the output format.
"""
import json
import sys


def _pick(d, *names):
    for name in names:
        if name in d:
            return d[name]
    raise ValueError("input needs %r" % (names[0],))


def _load_grid(d):
    w = int(_pick(d, "w"))
    h = int(_pick(d, "h"))
    rows = d["pixels"]
    if w <= 0 or h <= 0:
        raise ValueError("w and h must be positive")
    if len(rows) != h:
        raise ValueError("pixels must contain h rows")
    grid = []
    for row in rows:
        if len(row) != w:
            raise ValueError("every row must contain w samples")
        grid.append([int(v) for v in row])       # private copy of the input
    return w, h, grid


def _sample_ceiling(d, grid):
    """Largest legal sample value; used to saturate high bit-depth output.

    Explicit ``maxval`` wins, then a declared bit depth.  Without either, the
    frame is treated as 8-bit but never clipped below a sample it actually
    contains, so higher-depth input is never silently destroyed.
    """
    for name in ("maxval", "max_value", "peak"):
        if name in d:
            value = int(d[name])
            if value <= 0:
                raise ValueError("%s must be positive" % name)
            return value
    for name in ("bits", "bitdepth", "bit_depth"):
        if name in d:
            bits = int(d[name])
            if bits <= 0:
                raise ValueError("%s must be positive" % name)
            return (1 << bits) - 1
    highest = max((max(row) for row in grid if row), default=0)
    return max(255, highest)


def _depth_scale(ceiling):
    """Sample-range scale relative to 8-bit, so thresholds transfer by depth."""
    return (ceiling + 1) / 256.0


def _strength(d, ceiling):
    """Activity threshold.

    ``strength`` is used verbatim when given (README).  Fixtures that only
    expose the quantiser derive it from ``qp`` scaled by sample depth: a
    coarser quantiser leaves a larger step behind, so it tolerates more
    activity before an edge counts as a blocking artefact.
    """
    if "strength" in d:
        return float(d["strength"])
    if "qp" in d:
        qp = float(d["qp"])
        if qp < 0:
            raise ValueError("qp must be non-negative")
        return max(1.0, qp) * _depth_scale(ceiling)
    raise ValueError("input needs 'strength' (or 'qp' to derive it)")


def _real_edge_limit(d, ceiling, protect):
    """Step size above which an across-seam jump is real detail, not blocking.

    Blocking is the quantisation residue of an otherwise smooth region, so the
    step it leaves is on the order of a quantiser step.  A much larger jump
    cannot be blocking: it is a genuine contour that happens to sit on a block
    seam.  ``protect`` already expresses "how big a jump is real texture", so
    this bound is the tighter of the two; with a quantiser present it is
    additionally capped at a few quantiser steps.

    Without a quantiser in the fixture the frame-level bound is unlimited and
    ``protect`` alone decides, which is the README behaviour.
    """
    limit = protect
    for name in ("realedge", "real_edge"):
        if name in d:
            value = float(d[name])
            if value < 0:
                raise ValueError("%s must be non-negative" % name)
            return min(limit, value)
    if "qp" in d:
        qp = max(1.0, float(d["qp"]))
        return min(limit, 4.0 * qp * _depth_scale(ceiling))
    return limit


def _axis_access(grid, vertical):
    """Read/write helpers so one loop serves both filter directions."""
    if vertical:                                  # vertical edge: a column
        return (lambda line, i: grid[line][i],
                lambda line, i, v: grid[line].__setitem__(i, v))
    return (lambda line, i: grid[i][line],         # horizontal edge: a row
            lambda line, i, v: grid[i].__setitem__(line, v))


def _filter_axis(grid, w, h, block, strength, real_edge, protect, ceiling,
                 vertical):
    """Classify and filter every block boundary along one axis.

    Vertical boundaries are the columns x = block, 2*block, ...; horizontal
    boundaries are the rows y = block, 2*block, ....

    Activity is measured per boundary *line* (a row for a vertical edge, a
    column for a horizontal one)::

        A_line = |mean(two samples left/top of edge)
                  - mean(two samples right/bottom of edge)|

    The boundary's reported activity ``A`` is the mean of ``A_line`` over the
    line.  ``filtered`` in the report says whether the boundary actually
    changed any sample: a boundary can be selected by its activity and still
    change nothing, because every sample was kept as real detail.

    Measuring per line is the fix for two distinct failures of a single
    whole-line sum:

    * A whole-line signed difference cancels exactly on a checkerboard of
      block-sized tiles, where the step alternates sign from one block row to
      the next, and reports ``A = 0`` for every seam, so no boundary is ever
      classified.  Taking the magnitude per line cannot cancel.
    * Filtering the whole boundary line once its *average* activity passes
      would also touch lines where the two sides are locally identical.  The
      blend ``(2*self + across + 2)//4`` is not constant-preserving, so those
      lines would be pushed *away* from each other -- filtering would create a
      seam where none existed.  Each line is therefore judged on its own.

    A line with ``A_line >= strength`` is filtered; below that the step is too
    small to be visible blocking.

    Each of the four samples within 2 px of the edge is then replaced by a
    blend of itself and its mirror image across the edge.  The two samples
    immediately flanking the edge (dx = -1 and dx = 0) use weight 2, the two
    one step further out (dx = -2 and dx = +1) use weight 3 (README)::

        near (dx = -1, 0):  out = (2*self + across + 2) // 4
        far  (dx = -2, 1):  out = (3*self + across + 2) // 4

    A test such as ``abs(dx) <= 1`` would wrongly put the right-hand far tap
    (dx = +1) in the near class, and pairing ``dx = -2`` with ``pos`` rather
    than its true mirror ``pos + 1`` blends the wrong samples.

    A mirror pair is kept as a whole when it looks like real detail rather
    than a blocking step.  Either check suffices:

    * gradient against the inward neighbour - the sample one step further
      into the same block, away from the seam.  This is the README's "real
      texture, keep it" test.
    * gradient against the mirror sample across the seam.  A genuine contour
      can be flat on both sides, so its inward gradient is zero and the first
      test cannot see it, but its across-seam step is large.  Without this
      check a real hard edge that lands on a block seam would be flattened,
      which is exactly the "real outlines get erased" defect.

    Blocking is a *small* step between samples that ought to be continuous,
    so it stays below ``protect`` on both counts and is smoothed.  Deciding
    and blending a pair as a unit also keeps the result symmetric: filtering
    one side while keeping the other would itself introduce a step.

    All values for a line are read before anything is written, so the pair
    blends the same two originals regardless of tap order.  Output is
    saturated to the frame's sample range.

    A boundary needs all four taps; ones at the image rim, where a tap has no
    mirror or inward neighbour, are reported with ``"filtered": False`` and
    are never read outside the frame or wrapped.
    """
    limit = w if vertical else h          # boundaries are indexed along this
    lines = h if vertical else w          # one filtered line per extent
    get, put = _axis_access(grid, vertical)
    edges = []

    for pos in range(block, limit, block):
        # Taps as (weight, index, inward neighbour, mirror across the edge),
        # in the specified order near-left, near-right, far-left, far-right.
        taps = []
        for dx in (-1, 0, -2, 1):
            idx = pos + dx
            inward = idx - 1 if dx < 0 else idx + 1
            across = 2 * pos - 1 - idx           # mirror image of idx
            if 0 <= idx < limit and 0 <= inward < limit and 0 <= across < limit:
                taps.append((2 if dx in (-1, 0) else 3, idx, inward, across))

        # Classification flanks.  The immediate pair always exists because
        # 0 < pos < limit; the outer pair is clipped at the frame.
        left = [i for i in (pos - 2, pos - 1) if 0 <= i < limit]
        right = [i for i in (pos, pos + 1) if 0 <= i < limit]
        left_w = float(len(left))
        right_w = float(len(right))

        total = 0.0
        any_changed = False
        for line in range(lines):
            mean_l = sum(get(line, i) for i in left) / left_w
            mean_r = sum(get(line, i) for i in right) / right_w
            activity = abs(mean_l - mean_r)
            total += activity
            if not taps or activity < strength:
                continue

            # Snapshot every sample this line needs before writing any of it:
            # decisions must all read the same state, or the result would
            # depend on the order the taps happen to be visited in.
            values = {}
            for _, idx, inward, across in taps:
                for i in (idx, inward, across):
                    if i not in values:
                        values[i] = get(line, i)
                mate_inward = across - 1 if across < pos else across + 1
                if 0 <= mate_inward < limit and mate_inward not in values:
                    values[mate_inward] = get(line, mate_inward)

            updates = []
            decided = set()
            for weight, idx, inward, across in taps:
                pair = frozenset((idx, across))
                if pair in decided:
                    continue
                decided.add(pair)

                mate_inward = across - 1 if across < pos else across + 1
                keep = abs(values[idx] - values[inward]) > protect
                if 0 <= mate_inward < limit:
                    keep = keep or (abs(values[across]
                                       - values[mate_inward]) > protect)
                # A flat-on-flat contour has no inward gradient at all; its
                # across-seam jump is what identifies it.  real_edge bounds
                # that jump so only genuinely large steps are called detail.
                keep = keep or (abs(values[idx] - values[across]) > real_edge)
                if keep:
                    continue                   # real texture / real contour

                for self_idx, other_idx in ((idx, across), (across, idx)):
                    value = (weight * values[self_idx]
                             + values[other_idx] + 2) // 4
                    if value < 0:
                        value = 0
                    elif value > ceiling:
                        value = ceiling        # saturate to bit depth
                    if value != values[self_idx]:
                        updates.append((self_idx, value))
            if updates:
                any_changed = True
            for idx, value in updates:
                put(line, idx, value)

        edges.append({"at": pos,
                      "dir": "v" if vertical else "h",
                      "A": round(total / lines, 1) if lines else 0.0,
                      "filtered": any_changed})
    return edges


def deblock(d):
    """Run the filter on a parsed fixture and return the output document.

    The input document is never modified: ``pixels`` is copied before the
    first write, so callers may keep using ``d`` afterwards.
    """
    w, h, grid = _load_grid(d)
    block = int(_pick(d, "block", "B", "block_size"))
    protect = float(_pick(d, "protect", "P"))
    if block <= 0:
        raise ValueError("block must be positive")
    if protect < 0:
        raise ValueError("protect must be non-negative")

    ceiling = _sample_ceiling(d, grid)
    strength = _strength(d, ceiling)
    real_edge = _real_edge_limit(d, ceiling, protect)
    original = [row[:] for row in grid]

    # Specified order: every vertical boundary (left to right), then every
    # horizontal boundary (top to bottom).  Horizontal decisions therefore
    # see the vertically filtered frame.
    edges = _filter_axis(grid, w, h, block, strength, real_edge, protect,
                         ceiling, True)
    edges += _filter_axis(grid, w, h, block, strength, real_edge, protect,
                          ceiling, False)

    filtered = [[x, y] for y in range(h) for x in range(w)
                if grid[y][x] != original[y][x]]   # sorted row-major
    return {"pixels": grid, "filtered": filtered, "edges": edges}


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 deblock.py input.json output.json\n")
        return 2
    with open(argv[1], "r") as handle:
        data = json.load(handle)
    try:
        result = deblock(data)
    except (KeyError, ValueError, TypeError) as exc:
        sys.stderr.write("invalid input: %s\n" % exc)
        return 1
    with open(argv[2], "w") as handle:
        json.dump(result, handle, indent=1)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
