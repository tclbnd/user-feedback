#!/usr/bin/env python3
"""Surface-area-heuristic (SAH) BVH: build, nearest-hit traversal, CLI report.

    python3 bvh.py input.json output.json

Input
-----
``{"tris": [[[x,y,z], [x,y,z], [x,y,z]], ...],
   "rays": [{"o": [x,y,z], "d": [x,y,z]}, ...],
   "leaf": 4, "seed": 1}``

Output
------
``{"hits":    [{"t": distance, "tri": id}, ...],   # -1 / -1 on a miss
   "height":  tree height in nodes,
   "nodes":   total node count,
   "sah_cost": expected SAH cost (in intersection units),
   "digest":   sha1 over canonical hits + tree summary,
   "stats":    aggregate hit/miss, triangle and test counts,
   "ray_stats": [{"t","tri","hit","nodes","boxes","tris"}, ...]}``

Design
------
Build
  * 16-bin binned SAH: triangles are binned per axis by centroid and every
    bucket boundary is scored with the standard model
    ``C_trav + (SA_l * N_l + SA_r * N_r) / SA_parent``.
  * Small nodes (<= ``EXACT_LIMIT`` triangles) are split by an exact sweep
    over every distinct centroid plane on every axis, which removes bucket
    quantisation where it matters most; larger nodes keep the O(n) binned
    pass so the build stays near-linear in the triangle count.
  * Degeneracies: coincident centroids on all axes, zero-area parent boxes,
    duplicated / zero-area / point triangles.  A deterministic median split
    takes over when SAH has no valid plane, so the requested leaf cap is
    honoured and no child is ever empty.
  * Robustness: the median candidate is scored with the same cost model and
    replaces SAH only when it is locally cheaper, so no node is worse than a
    median split would have made it.

Traversal
  * Children are pushed in entry-parameter order (near child popped first)
    and re-tested against the current nearest hit on entry, so the tree is
    searched near-to-far with the search radius shrinking as hits are found.
  * Every triangle of every surviving leaf is intersected with the exact
    reference arithmetic: the result is the global nearest hit, and hit
    distances are bit-identical to a brute-force scan.

Determinism
  * Triangles are laid out in a canonical geometric order (rotation- and
    winding-independent vertex key) before the build, so permuting the input
    list yields the identical tree shape, SAH cost and hit set.
  * ``digest`` covers canonical hit geometry plus the tree summary and is
    therefore permutation invariant.
  * ``tri`` ids identify a triangle's geometry class: exactly duplicated
    triangles share one id, since they are interchangeable and there is no
    order-free way to tell them apart.  Every other id is a function of the
    triangle multiset only, so ids do not change under reordering.
"""

import hashlib
import json
import math
import sys

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
NBINS = 16          # SAH bins per axis
EXACT_LIMIT = 64    # nodes up to this size use exact (all-plane) SAH
C_TRAV = 1.0        # cost of visiting an interior node
C_ISECT = 1.0       # cost of one triangle intersection test
TMIN = 1e-9         # near epsilon (same as the reference implementation)
TMAX = 1e30         # far limit
DET_EPS = 1e-12     # Moller-Trumbore determinant epsilon
EXT_EPS = 1e-30     # centroid-extent epsilon for "all coincident"
SA_EPS = 1e-30      # surface-area epsilon
SLAB_EPS = 1e-12    # relative slack used *only* for bounding-box culling


# ---------------------------------------------------------------------------
# Box helpers.  Boxes are 6-tuples (mnx, mny, mnz, mxx, mxy, mxz).
# ---------------------------------------------------------------------------
def _finite(x):
    return math.isfinite(x)


def _box_area(mn, mx):
    dx = mx[0] - mn[0]
    dy = mx[1] - mn[1]
    dz = mx[2] - mn[2]
    if dx < 0.0 or dy < 0.0 or dz < 0.0:
        return 0.0
    return 2.0 * (dx * dy + dy * dz + dz * dx)


def _area6(box):
    return _box_area(box[0:3], box[3:6])


def _box_union(a, b):
    return (a[0] if a[0] < b[0] else b[0],
            a[1] if a[1] < b[1] else b[1],
            a[2] if a[2] < b[2] else b[2],
            a[3] if a[3] > b[3] else b[3],
            a[4] if a[4] > b[4] else b[4],
            a[5] if a[5] > b[5] else b[5])


def _box_valid(box):
    """A node box is valid iff it is present and never inverted.

    Zero-area (planar, linear, point) boxes are valid: they describe real
    degenerate geometry and must never be dropped.
    """
    if box is None or len(box) != 6:
        return False
    for i in range(3):
        if not (_finite(box[i]) and _finite(box[i + 3])):
            return False
        if box[i] > box[i + 3]:
            return False
    return True


# ---------------------------------------------------------------------------
# Triangle preparation
# ---------------------------------------------------------------------------
def _vertices(tri):
    """Coerce a triangle to a tuple of three (x, y, z) float tuples."""
    try:
        if len(tri) != 3:
            return None
        out = []
        for p in tri:
            if len(p) < 3:
                return None
            x = float(p[0])
            y = float(p[1])
            z = float(p[2])
            if not (_finite(x) and _finite(y) and _finite(z)):
                return None
            out.append((x, y, z))
        return (out[0], out[1], out[2])
    except (TypeError, ValueError, IndexError):
        return None


def _canonical_key(v):
    """Winding- and rotation-independent key of a triangle.

    Geometrically identical triangles share a key no matter how their vertex
    list was supplied or ordered.
    """
    pts = []
    for p in v:
        pts.append((0.0 if p[0] == 0.0 else p[0],
                    0.0 if p[1] == 0.0 else p[1],
                    0.0 if p[2] == 0.0 else p[2]))
    best = None
    for i in range(3):
        a = pts[i]
        b = pts[(i + 1) % 3]
        c = pts[(i + 2) % 3]
        for seq in ((a, b, c), (a, c, b)):
            if best is None or seq < best:
                best = seq
    return (best[0][0], best[0][1], best[0][2],
            best[1][0], best[1][1], best[1][2],
            best[2][0], best[2][1], best[2][2])


# Record layout: (cid, key, vertices, centroid, box_min, box_max)
#   cid       id of the triangle's geometry class; stable under reordering
#   vertices  kept in the *supplied* order so the intersection arithmetic is
#             bit-identical to a brute-force scan of the same input
CID, KEY, VERTS, CENT, BMIN, BMAX = 0, 1, 2, 3, 4, 5


def _record_from_tri(raw):
    v = _vertices(raw)
    if v is None:
        return None
    key = _canonical_key(v)
    cx = (v[0][0] + v[1][0] + v[2][0]) / 3.0
    cy = (v[0][1] + v[1][1] + v[2][1]) / 3.0
    cz = (v[0][2] + v[1][2] + v[2][2]) / 3.0
    if not (_finite(cx) and _finite(cy) and _finite(cz)):
        cx = cy = cz = 0.0
    mn = (min(v[0][0], v[1][0], v[2][0]),
          min(v[0][1], v[1][1], v[2][1]),
          min(v[0][2], v[1][2], v[2][2]))
    mx = (max(v[0][0], v[1][0], v[2][0]),
          max(v[0][1], v[1][1], v[2][1]),
          max(v[0][2], v[1][2], v[2][2]))
    return (0, key, v, (cx, cy, cz), mn, mx)


def _prepare(tris):
    """Parse and canonically sort the triangle list.

    Returns records ``(cid, key, verts, centroid, box_min, box_max)``.

    Sorting by ``(key, verts)`` makes the build independent of the caller's
    triangle order.  ``cid`` is the index of the triangle's *geometry class*:
    exactly duplicated triangles share one id, because they are
    interchangeable and there is no order-free way to tell them apart.  Ids
    are therefore a deterministic function of the multiset of triangles.
    """
    records = []
    for raw in tris:
        r = _record_from_tri(raw)
        if r is not None:
            records.append(r)
    records.sort(key=lambda r: (r[KEY], r[VERTS]))

    out = []
    prev = None
    cid = -1
    for r in records:
        ident = (r[KEY], r[VERTS])
        if ident != prev:
            prev = ident
            cid += 1
        out.append((cid, r[KEY], r[VERTS], r[CENT], r[BMIN], r[BMAX]))
    return out


def _records_box(records, cids):
    """Union box of the given records (never empty for a non-empty ``cids``)."""
    r = records[cids[0]]
    mnx, mny, mnz = r[BMIN]
    mxx, mxy, mxz = r[BMAX]
    for cid in cids[1:]:
        r = records[cid]
        bmn = r[BMIN]
        bmx = r[BMAX]
        if bmn[0] < mnx:
            mnx = bmn[0]
        if bmn[1] < mny:
            mny = bmn[1]
        if bmn[2] < mnz:
            mnz = bmn[2]
        if bmx[0] > mxx:
            mxx = bmx[0]
        if bmx[1] > mxy:
            mxy = bmx[1]
        if bmx[2] > mxz:
            mxz = bmx[2]
    return (mnx, mny, mnz, mxx, mxy, mxz)


# ---------------------------------------------------------------------------
# SAH split search
#
# Both evaluators use the same cost model
#     C_TRAV + (SA_l * N_l + SA_r * N_r) / SA_parent
# and only consider planes between distinct centroid values, so neither can
# produce an empty child.
# ---------------------------------------------------------------------------
def _prefix_areas(records, order):
    """Running union boxes over ``order`` from both ends, as flat tuples.

    Returns ``(pre, post)`` where ``pre[i]`` is the union box of
    ``order[:i+1]`` and ``post[i]`` that of ``order[i:]``.
    """
    n = len(order)
    pre = [None] * n
    mnx = mny = mnz = float("inf")
    mxx = mxy = mxz = float("-inf")
    for i in range(n):
        r = records[order[i]]
        bmn = r[BMIN]
        bmx = r[BMAX]
        if bmn[0] < mnx:
            mnx = bmn[0]
        if bmn[1] < mny:
            mny = bmn[1]
        if bmn[2] < mnz:
            mnz = bmn[2]
        if bmx[0] > mxx:
            mxx = bmx[0]
        if bmx[1] > mxy:
            mxy = bmx[1]
        if bmx[2] > mxz:
            mxz = bmx[2]
        pre[i] = (mnx, mny, mnz, mxx, mxy, mxz)
    post = [None] * n
    mnx = mny = mnz = float("inf")
    mxx = mxy = mxz = float("-inf")
    for i in range(n - 1, -1, -1):
        r = records[order[i]]
        bmn = r[BMIN]
        bmx = r[BMAX]
        if bmn[0] < mnx:
            mnx = bmn[0]
        if bmn[1] < mny:
            mny = bmn[1]
        if bmn[2] < mnz:
            mnz = bmn[2]
        if bmx[0] > mxx:
            mxx = bmx[0]
        if bmx[1] > mxy:
            mxy = bmx[1]
        if bmx[2] > mxz:
            mxz = bmx[2]
        post[i] = (mnx, mny, mnz, mxx, mxy, mxz)
    return pre, post


def _exact_split(records, cids, parent_box):
    """Exact SAH over every distinct centroid plane on every axis.

    Returns ``(cost, axis, left, right, left_box, right_box)`` for the
    cheapest split, or ``None``.  The candidate boxes are returned with the
    partition so the builder does not have to recompute them.
    """
    n = len(cids)
    psa = _area6(parent_box)
    if n < 2 or psa <= SA_EPS:
        return None

    best = None
    for axis in range(3):
        # ``sort`` is stable, so sorting on the centroid alone keeps a
        # deterministic tie-break (children inherit their parent's order,
        # which derives from the canonical order fixed in ``_prepare``)
        # without re-evaluating (KEY, VERTS) on every comparison.  Triangles
        # with equal centroids are interchangeable for the candidate planes:
        # splits inside such a run are skipped anyway.
        order = sorted(cids, key=lambda c: records[c][CENT][axis])
        pre, post = _prefix_areas(records, order)
        for i in range(n - 1):
            if records[order[i]][CENT][axis] == records[order[i + 1]][CENT][axis]:
                continue  # not a real plane: both sides would be identical
            nl = i + 1
            nr = n - nl
            bl = pre[i]
            br = post[i + 1]
            cost = C_TRAV + (_area6(bl) * nl + _area6(br) * nr) / psa
            if not _finite(cost):
                continue
            balance = abs(nl - nr)
            if best is None:
                best = (cost, balance, axis, order, i, bl, br)
                continue
            delta = cost - best[0]
            if delta < -1e-12 or (abs(delta) <= 1e-12 and balance < best[1]):
                best = (cost, balance, axis, order, i, bl, br)

    if best is None:
        return None
    cost, _, axis, order, i, bl, br = best
    return cost, axis, order[:i + 1], order[i + 1:], bl, br


def _binned_split(records, cids, parent_box):
    """16-bin binned SAH.

    Returns ``(cost, axis, left, right, left_box, right_box)`` for the
    cheapest bucket boundary, or ``None`` when no axis has a usable centroid
    spread.
    """
    n = len(cids)
    psa = _area6(parent_box)
    if n < 2 or psa <= SA_EPS:
        return None

    # The partition and the centroid extents are the same for every axis, so
    # the axis loop only walks the triangles once (per-axis binning below is
    # over the small NBINS arrays, not over the triangles).
    cent = [records[cid][CENT] for cid in cids]
    bmins = [records[cid][BMIN] for cid in cids]
    bmaxs = [records[cid][BMAX] for cid in cids]

    best = None
    for axis in range(3):
        cmin = cmax = cent[0][axis]
        for c in cent:
            x = c[axis]
            if x < cmin:
                cmin = x
            elif x > cmax:
                cmax = x
        extent = cmax - cmin
        if extent <= EXT_EPS:
            continue
        scale = NBINS / extent

        cnt = [0] * NBINS
        bmnx = [float("inf")] * NBINS
        bmny = [float("inf")] * NBINS
        bmnz = [float("inf")] * NBINS
        bmxx = [float("-inf")] * NBINS
        bmxy = [float("-inf")] * NBINS
        bmxz = [float("-inf")] * NBINS

        for i in range(n):
            b = int((cent[i][axis] - cmin) * scale)
            if b < 0:
                b = 0
            elif b >= NBINS:
                b = NBINS - 1
            cnt[b] += 1
            bmn = bmins[i]
            bmx = bmaxs[i]
            if bmn[0] < bmnx[b]:
                bmnx[b] = bmn[0]
            if bmn[1] < bmny[b]:
                bmny[b] = bmn[1]
            if bmn[2] < bmnz[b]:
                bmnz[b] = bmn[2]
            if bmx[0] > bmxx[b]:
                bmxx[b] = bmx[0]
            if bmx[1] > bmxy[b]:
                bmxy[b] = bmx[1]
            if bmx[2] > bmxz[b]:
                bmxz[b] = bmx[2]

        # Prefix accumulation over bins 0..k.
        pl_n = [0] * NBINS
        pl_box = [None] * NBINS
        acc_n = 0
        acc = None
        for k in range(NBINS):
            if cnt[k]:
                acc_n += cnt[k]
                b = (bmnx[k], bmny[k], bmnz[k], bmxx[k], bmxy[k], bmxz[k])
                acc = b if acc is None else _box_union(acc, b)
            pl_n[k] = acc_n
            pl_box[k] = acc

        # Suffix accumulation over bins k..NBINS-1.
        sr_n = [0] * NBINS
        sr_box = [None] * NBINS
        acc_n = 0
        acc = None
        for k in range(NBINS - 1, -1, -1):
            if cnt[k]:
                acc_n += cnt[k]
                b = (bmnx[k], bmny[k], bmnz[k], bmxx[k], bmxy[k], bmxz[k])
                acc = b if acc is None else _box_union(acc, b)
            sr_n[k] = acc_n
            sr_box[k] = acc

        for k in range(1, NBINS):
            nl = pl_n[k - 1]
            nr = sr_n[k]
            if nl == 0 or nr == 0:
                continue
            bl = pl_box[k - 1]
            br = sr_box[k]
            cost = C_TRAV + (_area6(bl) * nl + _area6(br) * nr) / psa
            if not _finite(cost):
                continue
            balance = abs(nl - nr)
            if best is None:
                best = (cost, balance, axis, k, cmin, scale, bl, br)
                continue
            delta = cost - best[0]
            if delta < -1e-12 or (abs(delta) <= 1e-12 and balance < best[1]):
                best = (cost, balance, axis, k, cmin, scale, bl, br)

    if best is None:
        return None

    cost, _, axis, k, cmin, scale, bl, br = best
    left = []
    right = []
    for cid in cids:
        b = int((records[cid][CENT][axis] - cmin) * scale)
        if b < 0:
            b = 0
        elif b >= NBINS:
            b = NBINS - 1
        (left if b < k else right).append(cid)
    if not left or not right:
        return None
    return cost, axis, left, right, bl, br


def _median_split(records, cids, parent_box=None):
    """Deterministic median split; always yields two non-empty halves.

    Axis choice: widest centroid spread; if every centroid coincides, widest
    triangle-bounds spread; if *that* also vanishes (fully coincident
    geometry) the canonical order is simply halved.

    Returns ``(axis, left, right, left_box, right_box, cost)``.  The cost uses
    the same model as :func:`_partition_cost`, so the builder can compare the
    median candidate with the SAH candidate without rebuilding boxes.
    """
    axis = 0
    spread = -1.0
    for a in range(3):
        mn = mx = records[cids[0]][CENT][a]
        for cid in cids[1:]:
            x = records[cid][CENT][a]
            if x < mn:
                mn = x
            elif x > mx:
                mx = x
        s = mx - mn
        if s > spread:
            spread = s
            axis = a

    if spread > EXT_EPS:
        # Stable sort on the centroid only: ``cids`` is canonical already.
        ordered = sorted(cids, key=lambda c: records[c][CENT][axis])
    else:
        axis = 0
        spread = -1.0
        for a in range(3):
            mn = mx = records[cids[0]][BMIN][a]
            for cid in cids[1:]:
                r = records[cid]
                if r[BMIN][a] < mn:
                    mn = r[BMIN][a]
                if r[BMAX][a] > mx:
                    mx = r[BMAX][a]
            s = mx - mn
            if s > spread:
                spread = s
                axis = a
        if spread > EXT_EPS:
            ordered = sorted(cids, key=lambda c: ((records[c][BMIN][axis] +
                                                   records[c][BMAX][axis]) * 0.5,
                                                  records[c][KEY],
                                                  records[c][VERTS]))
        else:
            ordered = cids  # canonical order is already deterministic

    mid = len(ordered) // 2
    if mid < 1:
        mid = 1
    elif mid >= len(ordered):
        mid = len(ordered) - 1
    left = ordered[:mid]
    right = ordered[mid:]
    lbox = _records_box(records, left)
    rbox = _records_box(records, right)
    cost = None
    if parent_box is not None:
        cost = _partition_cost(lbox, len(left), rbox, len(right), parent_box)
    return axis, left, right, lbox, rbox, cost


def _partition_cost(lbox, nl, rbox, nr, parent_box):
    """SAH cost of a partition given the child boxes and counts."""
    psa = _area6(parent_box)
    if psa > SA_EPS:
        return C_TRAV + (_area6(lbox) * nl + _area6(rbox) * nr) / psa
    # Zero-area parent: fall back to triangle counts, which is exactly the
    # leaf term of the cost model.
    return C_TRAV + float(nl + nr)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build(tris, leaf=4, strategy="sah"):
    """Build a BVH over ``tris``.

    ``strategy``: ``"sah"`` (default) or ``"median"`` (the defect baseline,
    kept so its cost can be measured against SAH).

    Node dict: ``{"box": 6-tuple, "n": triangles, "axis": split axis}`` plus
    either ``{"leaf": [records]}`` or ``{"l": node, "r": node}``.  Heights,
    node counts and SAH costs are annotated bottom-up by :func:`tree_cost`.
    Returns ``None`` for an empty (or fully invalid) triangle list.
    """
    try:
        leaf = int(leaf)
    except (TypeError, ValueError):
        leaf = 4
    if leaf < 1:
        leaf = 1

    records = _prepare(tris)
    if not records:
        return None

    root = {"box": None, "n": len(records)}
    stack = [(root, list(range(len(records))))]
    while stack:
        node, ids = stack.pop()
        node["n"] = len(ids)
        node["box"] = _records_box(records, ids)
        if len(ids) <= leaf:
            node["leaf"] = [records[c] for c in ids]
            continue

        chosen = None
        mode = "median"
        if strategy == "sah":
            if len(ids) <= EXACT_LIMIT:
                chosen = _exact_split(records, ids, node["box"])
                mode = "exact"
            else:
                chosen = _binned_split(records, ids, node["box"])
                mode = "binned"

        med_axis, med_left, med_right, med_lbox, med_rbox, med_cost = \
            _median_split(records, ids, node["box"])

        if chosen is None:
            # Baseline build, or no usable SAH plane (all centroids coincident
            # / zero-area parent): the deterministic median split is used.
            axis, left, right = med_axis, med_left, med_right
            lbox, rbox = med_lbox, med_rbox
            if strategy == "sah":
                mode = "median-degenerate"
        else:
            sah_cost, sah_axis, sah_l, sah_r, lbox, rbox = chosen
            if med_cost < sah_cost:
                # The median split is locally cheaper: keep it.  This also
                # guarantees no node is worse than a median split.
                axis, left, right = med_axis, med_left, med_right
                lbox, rbox = med_lbox, med_rbox
                mode = "median-cheaper"
            else:
                axis, left, right = sah_axis, sah_l, sah_r

        if not left or not right:
            # Unreachable for len(ids) > leaf >= 1; never emit a bad node.
            node["leaf"] = [records[c] for c in ids]
            continue

        node["axis"] = axis
        node["split_mode"] = mode
        node["l"] = {"box": lbox, "n": len(left)}
        node["r"] = {"box": rbox, "n": len(right)}
        stack.append((node["r"], right))
        stack.append((node["l"], left))

    tree_cost(root)
    return root


def tree_cost(tree):
    """Annotate every node with ``height``, ``count`` and ``cost``.

    A leaf costs ``N * C_ISECT``.  An interior node costs
    ``C_TRAV + w_l * cost_l + w_r * cost_r`` with each child weighted by its
    relative surface area (by triangle count when the parent box has no area,
    which keeps degenerate trees well defined).
    Returns the root cost (0.0 for an empty tree).  Always recomputed, so the
    function is idempotent and safe to call on a tree that changed.
    """
    if tree is None:
        return 0.0
    stack = [(tree, False)]
    while stack:
        node, expanded = stack.pop()
        if "leaf" in node:
            n = len(node["leaf"])
            node["height"] = 1
            node["count"] = 1
            node["cost"] = float(n * C_ISECT)
            continue
        if not expanded:
            stack.append((node, True))
            stack.append((node["r"], False))
            stack.append((node["l"], False))
            continue
        psa = _area6(node["box"])
        if psa > SA_EPS:
            wl = _area6(node["l"]["box"]) / psa
            wr = _area6(node["r"]["box"]) / psa
        else:
            total = float(node["n"]) or 1.0
            wl = node["l"]["n"] / total
            wr = node["r"]["n"] / total
        cost = C_TRAV + wl * node["l"]["cost"] + wr * node["r"]["cost"]
        node["cost"] = cost if _finite(cost) else 0.0
        nl = node["l"]
        nr = node["r"]
        h = nl["height"] if nl["height"] > nr["height"] else nr["height"]
        node["height"] = 1 + h
        node["count"] = 1 + nl["count"] + nr["count"]
    return tree["cost"]


def tree_shape(tree):
    """``(height, node_count)``; the empty tree is ``(0, 0)``."""
    if tree is None:
        return 0, 0
    return tree["height"], tree["count"]


# ---------------------------------------------------------------------------
# Intersection and traversal
# ---------------------------------------------------------------------------
def moller(o, d, tri):
    """Two-sided Moller-Trumbore.

    The expression order matches the brute-force reference, so every accepted
    hit has a bit-identical distance.
    """
    ax, ay, az = tri[0]
    e1x = tri[1][0] - ax
    e1y = tri[1][1] - ay
    e1z = tri[1][2] - az
    e2x = tri[2][0] - ax
    e2y = tri[2][1] - ay
    e2z = tri[2][2] - az
    dx, dy, dz = d
    px = dy * e2z - dz * e2y
    py = dz * e2x - dx * e2z
    pz = dx * e2y - dy * e2x
    det = e1x * px + e1y * py + e1z * pz
    if abs(det) < DET_EPS:
        return None
    inv = 1.0 / det
    sx = o[0] - ax
    sy = o[1] - ay
    sz = o[2] - az
    u = (sx * px + sy * py + sz * pz) * inv
    if u < 0.0 or u > 1.0:
        return None
    qx = sy * e1z - sz * e1y
    qy = sz * e1x - sx * e1z
    qz = sx * e1y - sy * e1x
    v = (dx * qx + dy * qy + dz * qz) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2x * qx + e2y * qy + e2z * qz) * inv
    return t if _finite(t) else None


def _prunable(tn, best_t):
    """True only when a node is *clearly* beyond the current nearest hit.

    Culling must tolerate the rounding difference between a bounding-box slab
    parameter and the Moller-Trumbore parameter of a triangle inside that box;
    otherwise a candidate a single ULP nearer than the current hit could be
    pruned and the result would no longer match the brute-force reference.
    The slack can only admit extra candidates, never remove real ones.
    """
    return tn > best_t + SLAB_EPS * (1.0 + abs(tn) + abs(best_t))


def _box_interval(box, o, d, tmin, tmax):
    """Slab test returning ``(entry, exit)`` or ``None``.

    Zero direction components are tested exactly (no reciprocal).  A tiny
    relative slack is applied only when a slab reports a miss, so rounding can
    never prune a box whose triangles the intersection test would accept --
    it can only admit extra candidates.
    """
    for i in range(3):
        di = d[i]
        lo = box[i]
        hi = box[i + 3]
        if di == 0.0:
            if o[i] < lo or o[i] > hi:
                return None
        else:
            inv = 1.0 / di
            t0 = (lo - o[i]) * inv
            t1 = (hi - o[i]) * inv
            if t0 > t1:
                t0, t1 = t1, t0
            if t0 > tmin:
                tmin = t0
            if t1 < tmax:
                tmax = t1
            if tmin > tmax:
                slack = SLAB_EPS * (1.0 + abs(tmin) + abs(tmax))
                if tmin > tmax + slack:
                    return None
    if tmin > tmax:
        tmin = tmax
    return tmin, tmax


def trace(tree, o, d, tn=TMIN, tf=TMAX):
    """Nearest-hit traversal inside the parameter range ``(tn, tf)``.

    Returns ``(record, t, cid, stats)``, or ``(None, None, -1, stats)``.

    Children are pushed in entry-parameter order and popped nearest-first,
    and each popped node is re-tested against the current nearest hit, so the
    tree is searched near-to-far while the search radius shrinks.  Every
    triangle of every surviving leaf is tested, so the returned hit is the
    global nearest one (not merely the first one found).

    ``tn``/``tf`` narrow the search interval; they are pushed into the root
    slab test and into every triangle acceptance test, so a narrowed search
    also prunes more.
    """
    stats = {"nodes": 0, "boxes": 0, "tris": 0}
    if tree is None:
        return None, None, -1, stats
    for x in (o[0], o[1], o[2], d[0], d[1], d[2]):
        if not _finite(x):
            return None, None, -1, stats

    stats["boxes"] += 1
    root_iv = _box_interval(tree["box"], o, d, tn, tf)
    if root_iv is None:
        return None, None, -1, stats

    best_t = tf
    best_rec = None
    best_cid = -1
    # Entry/exit parameters of a node are box-intersection bounds; the global
    # search lower bound stays separate so a triangle can never be rejected
    # merely because the box entry rounded up onto its hit distance.
    stack = [(tree, root_iv[0], root_iv[1])]

    while stack:
        node, tnode, tf_node = stack.pop()
        if _prunable(tnode, best_t):
            continue
        stats["nodes"] += 1

        leaf = node.get("leaf")
        if leaf is not None:
            for rec in leaf:
                stats["tris"] += 1
                h = moller(o, d, rec[VERTS])
                if h is None or h <= tn or h > best_t:
                    continue
                # Strictly nearer wins; an exact tie goes to the lower
                # canonical id so the result cannot depend on visit order.
                if h < best_t or best_rec is None or rec[CID] < best_cid:
                    best_t = h
                    best_rec = rec
                    best_cid = rec[CID]
            continue

        iv_l = _box_interval(node["l"]["box"], o, d, tnode, best_t)
        iv_r = _box_interval(node["r"]["box"], o, d, tnode, best_t)
        stats["boxes"] += 2
        if iv_l is not None and iv_r is not None:
            # Push the far child first: the near child is popped and handled
            # first, tightening best_t before the far child is ever entered.
            if iv_l[0] <= iv_r[0]:
                stack.append((node["r"], iv_r[0], iv_r[1]))
                stack.append((node["l"], iv_l[0], iv_l[1]))
            else:
                stack.append((node["l"], iv_l[0], iv_l[1]))
                stack.append((node["r"], iv_r[0], iv_r[1]))
        elif iv_l is not None:
            stack.append((node["l"], iv_l[0], iv_l[1]))
        elif iv_r is not None:
            stack.append((node["r"], iv_r[0], iv_r[1]))

    if best_rec is None:
        return None, None, -1, stats
    return best_rec, best_t, best_cid, stats


def hit(tree, o, d, tn=TMIN, tf=TMAX):
    """Nearest hit distance strictly inside ``(tn, tf)``, or ``None``."""
    rec, t, _, _ = trace(tree, o, d, tn, tf)
    if rec is None or t is None or not (tn < t < tf):
        return None
    return t


def brute_force_hit(tris, o, d, tn=TMIN, tf=TMAX):
    """Reference scan over a raw triangle list (no acceleration)."""
    best = None
    for raw in tris:
        v = _vertices(raw)
        if v is None:
            continue
        h = moller(o, d, v)
        if h is not None and tn < h < tf and (best is None or h < best):
            best = h
    return best


def bbox(ts):
    """Axis-aligned bounds of a triangle list, as ``([mn...], [mx...])``."""
    mn = [float("inf")] * 3
    mx = [float("-inf")] * 3
    for raw in ts:
        v = _vertices(raw)
        if v is None:
            continue
        for p in v:
            for i in range(3):
                if p[i] < mn[i]:
                    mn[i] = p[i]
                if p[i] > mx[i]:
                    mx[i] = p[i]
    return mn, mx


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _fmt(x):
    """Stable text form of a float (``-0.0`` normalised, round-trip digits)."""
    if x == 0.0:
        x = 0.0
    return format(x, ".17g")


def _fmt_key(key):
    return "|".join(_fmt(x) for x in key)


def _ray_args(r):
    try:
        o = [float(r["o"][0]), float(r["o"][1]), float(r["o"][2])]
        d = [float(r["d"][0]), float(r["d"][1]), float(r["d"][2])]
    except (KeyError, TypeError, ValueError, IndexError):
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return o, d


def _walk(tree):
    stack = [tree] if tree is not None else []
    while stack:
        node = stack.pop()
        yield node
        if "leaf" not in node:
            stack.append(node["l"])
            stack.append(node["r"])


def assert_valid(tree):
    """Structural post-conditions: no empty box, leaf or child."""
    for node in _walk(tree):
        if not _box_valid(node["box"]):
            raise AssertionError("BVH node with a missing or empty box")
        if "leaf" in node:
            if not node["leaf"]:
                raise AssertionError("BVH leaf with no triangles")
            if node["n"] != len(node["leaf"]):
                raise AssertionError("BVH leaf count mismatch")
        else:
            if node["l"] is None or node["r"] is None:
                raise AssertionError("BVH node with a missing child")
            if node["l"]["n"] <= 0 or node["r"]["n"] <= 0:
                raise AssertionError("BVH node with an empty child")
            if node["n"] != node["l"]["n"] + node["r"]["n"]:
                raise AssertionError("BVH node count mismatch")


def summarise(tree):
    """Tree summary shared by the output file and the digest."""
    if tree is None:
        return {"height": 0, "nodes": 0, "leaves": 0, "triangles": 0,
                "sah_cost": 0.0, "max_leaf": 0, "axis_splits": [0, 0, 0]}
    leaves = 0
    triangles = 0
    max_leaf = 0
    axes = [0, 0, 0]
    for node in _walk(tree):
        if "leaf" in node:
            leaves += 1
            n = len(node["leaf"])
            triangles += n
            if n > max_leaf:
                max_leaf = n
        else:
            axes[node["axis"]] += 1
    return {"height": tree["height"],
            "nodes": tree["count"],
            "leaves": leaves,
            "triangles": triangles,
            "sah_cost": tree["cost"],
            "max_leaf": max_leaf,
            "axis_splits": axes}


def digest_of(summary, digest_hits):
    """SHA1 over canonical hit geometry plus the tree summary."""
    payload = {
        "height": summary["height"],
        "nodes": summary["nodes"],
        "leaves": summary["leaves"],
        "triangles": summary["triangles"],
        "max_leaf": summary["max_leaf"],
        "axis_splits": summary["axis_splits"],
        "sah_cost": _fmt(summary["sah_cost"]),
        "hits": digest_hits,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def run(req):
    """Compute the full report for a parsed request (pure function)."""
    tree = build(req.get("tris", []), req.get("leaf", 4))
    assert_valid(tree)
    summary = summarise(tree)

    hits = []
    ray_stats = []
    digest_hits = []
    n_hit = 0
    for ray in req.get("rays", []):
        o, d = _ray_args(ray)
        rec, t, cid, st = trace(tree, o, d)
        if rec is None:
            hits.append({"t": -1.0, "tri": -1})
            digest_hits.append(["-1", ""])
            t_out = -1.0
            tri_out = -1
        else:
            n_hit += 1
            hits.append({"t": t, "tri": cid})
            digest_hits.append([_fmt(t), _fmt_key(rec[KEY])])
            t_out = t
            tri_out = cid
        ray_stats.append({"t": t_out, "tri": tri_out,
                          "hit": 0 if rec is None else 1,
                          "nodes": st["nodes"],
                          "boxes": st["boxes"],
                          "tris": st["tris"]})

    return {
        "hits": hits,
        "height": summary["height"],
        "nodes": summary["nodes"],
        "sah_cost": summary["sah_cost"],
        "digest": digest_of(summary, digest_hits),
        "stats": {
            "rays": len(ray_stats),
            "hits": n_hit,
            "misses": len(ray_stats) - n_hit,
            "triangles": summary["triangles"],
            "leaves": summary["leaves"],
            "max_leaf": summary["max_leaf"],
            "height": summary["height"],
            "nodes": summary["nodes"],
            "sah_cost": summary["sah_cost"],
            "axis_splits": summary["axis_splits"],
            "triangle_tests": sum(r["tris"] for r in ray_stats),
            "box_tests": sum(r["boxes"] for r in ray_stats),
        },
        "ray_stats": ray_stats,
    }


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 bvh.py input.json output.json\n")
        return 2
    with open(argv[1], "r") as f:
        req = json.load(f)
    result = run(req)
    with open(argv[2], "w") as f:
        json.dump(result, f, separators=(",", ":"), sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
