#!/usr/bin/env python3
"""Per-corner tangent-space reconstruction with mirrored UV island support.

Usage::

    python3 tangent.py mesh.json output.json

Input ``mesh.json``::

    {"pos":  [[x, y, z], ...],
     "uv":   [[u, v], ...],        # per-vertex (len == len(pos)) or
                                   # per-corner (len == 3*len(tris))
     "tris": [[a, b, c], ...]}

Output ``output.json``::

    {"tangents":        [[tx, ty, tz, w], ...]  # one per corner, tris order
     "bitangent_sign":  [w, ...]                # same handedness, per corner
     "corner_vertex":   [vid, ...]              # position index per corner
     "split_vertices":  n                       # number of split tangent groups
     "degenerate":      d                       # zero-uv-area triangles
     "degenerate_corners": 3*d
     "fallback_keys":   k                       # groups using the fallback basis
     "fallback_corners":kc                      # corners using it
     "split_tangents":  [[vid,h,tx,ty,tz,w,n_valid,n_degen], ...]  # canonical
     "digest":          "..."}                  # sha256 of split_tangents

Method
------
For each triangle we solve the classic dPos/dUV system

    T_tri = (e1*dv2 - e2*dv1)/det,  B_tri = (e2*du1 - e1*du2)/det,
    det   = du1*dv2 - dv1*du2,      e1 = p1-p0, e2 = p2-p0

and keep the sign of ``det`` as the corner handedness ``h`` (+1 or -1).

Tangents are accumulated per *split group* ``(position index, handedness)``.
The split is on handedness, not on UV:

* a UV **seam** (u jumping e.g. 1.0 -> 0.0) is pure parameterisation, the
  surface is the same on both sides, so both sides must agree on the tangent
  and are therefore accumulated together;
* a **mirrored island** flips ``h``; the position is shared with a normal
  chart, so keeping ``h`` in the key puts the two charts into separate groups
  and prevents the usual "mirrored island cancels / inverts the tangent" bug.

Both sums use angle weights: tangents use the UV-space corner angles (as
Mikktspace does, which removes the bias of splitting a quad into two triangles),
the vertex normal uses the position-space corner angles.  The normal is
accumulated **per position**, never per split group, because it is pure
geometry and must not depend on the UV chart -- that shared, correct normal is
what makes Gram-Schmidt

    T = normalize(T_raw - N * dot(N, T_raw))

reproduce the analytic tangent on both sides of a mirrored pair.

``w`` is the corner handedness itself, so ``B = w * cross(N, T)``.  On a
mirrored island ``T`` flips while ``N`` does not, hence ``cross(N, T)`` points
the opposite way and ``w`` flips with it: mirrored corners get ``w = -1``,
normal corners ``w = +1``, and ``|cross(N, T)| = 1`` for both.

Zero UV area (``|det| <= 1e-12 * |du1| * |du2|``, or a zero UV edge) is
*degenerate*: such a triangle feeds the normal only.  A split group left without
any usable tangent falls back to the documented basis around its vertex normal

    ref = (1,0,0) when |N.x| <= 0.9 else (0,1,0)
    T   = normalize(ref - N*dot(N,ref)),  w = h

(``T = (1,0,0)``, ``w = 1`` if the normal itself is zero).

Order independence
------------------
Each triangle is first rotated cyclically into its canonical corner order and
every group's contributions are summed in a total order derived from the
triangle's own data, so permuting the face list (or the cyclic start of a face)
reproduces the same groups, the same floating point sums and therefore the same
digest.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys

UV_EPS = 1e-12      # relative zero-area threshold on det
TINY = 1e-30        # "zero length" guard
DIGEST_DIGITS = 9   # rounding used for the order-independent digest


# ---------------------------------------------------------------------------
# tiny vector helpers (plain tuples, no numpy dependency)
# ---------------------------------------------------------------------------
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _normalize(a):
    n = _length(a)
    if n <= TINY:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def _fallback_basis(n):
    """Deterministic right-handed basis around ``n`` -> (T, B, N)."""
    n = _normalize(n)
    if n is None:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    # Pick the world axis least aligned with the normal to avoid cancellation.
    ref = (1.0, 0.0, 0.0) if abs(n[0]) <= 0.9 else (0.0, 1.0, 0.0)
    d = _dot(n, ref)
    t = _normalize((ref[0] - n[0] * d,
                    ref[1] - n[1] * d,
                    ref[2] - n[2] * d))
    if t is None:                      # never happens for a unit normal
        t = (1.0, 0.0, 0.0)
    return t, _cross(n, t), n


# ---------------------------------------------------------------------------
# input layout
# ---------------------------------------------------------------------------
def _corner_uvs(mesh):
    """Flatten UVs to one entry per corner; returns (uv_per_corner, mode)."""
    pos = mesh["pos"]
    tris = mesh["tris"]
    uv = mesh["uv"]
    n_corners = 3 * len(tris)

    corner_mode = len(uv) == n_corners and len(uv) != len(pos)
    if not corner_mode and len(uv) != len(pos):
        raise ValueError(
            "uv needs one entry per position (%d) or per corner (%d), got %d"
            % (len(pos), n_corners, len(uv)))
    if corner_mode:
        flat = [tuple(uv[3 * t + k]) for t in range(len(tris)) for k in range(3)]
        return flat, "corner"
    flat = [tuple(uv[vid]) for tri in tris for vid in tri]
    return flat, "vertex"


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------
def _corner_angles(pa, pb, pc):
    """Opening angle of each corner of ``(a, b, c)`` -> (w0, w1, w2).

    Works in any dimension (used both for the UV triangle and for the position
    triangle).  Falls back to 1/3 each if an angle is not measurable.
    """
    out = []
    for a, b, c in ((pa, pb, pc), (pb, pc, pa), (pc, pa, pb)):
        n = len(a)
        ax = [b[i] - a[i] for i in range(n)]
        bx = [c[i] - a[i] for i in range(n)]
        la = math.sqrt(sum(x * x for x in ax))
        lb = math.sqrt(sum(x * x for x in bx))
        if la <= TINY or lb <= TINY:
            return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
        cosang = sum(ax[i] * bx[i] for i in range(n)) / (la * lb)
        out.append(math.acos(max(-1.0, min(1.0, cosang))))
    total = out[0] + out[1] + out[2]
    if total <= TINY:
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return (out[0] / total, out[1] / total, out[2] / total)


def _canonical_triangle(uvs, tri):
    """Rotate a triangle into a canonical corner order.

    The dPos/dUV solve is invariant under cyclic rotation, but the floating
    point rounding is not, so we always start at the corner with the smallest
    ``(vid, u, v)`` key.  This makes the result independent of the face list
    order *and* of the starting corner of each face.  Returns
    ``(canonical_corners, rotation_offset)`` where ``canonical_corners[k]`` is
    the original corner ``(offset + k) % 3``.
    """
    corners = [(tri[0], uvs[0]), (tri[1], uvs[1]), (tri[2], uvs[2])]
    best = 0
    best_key = None
    for s in range(3):
        key = tuple(corners[(s + k) % 3] for k in range(3))
        if best_key is None or key < best_key:
            best_key = key
            best = s
    return [corners[(best + k) % 3] for k in range(3)], best


def run(mesh):
    pos = mesh["pos"]
    tris = mesh["tris"]
    corner_uv, uv_mode = _corner_uvs(mesh)

    n_tris = len(tris)
    n_corners = 3 * n_tris

    # split group (position, handedness) -> list of contributions.
    # The normal is *not* split: it is pure geometry and must not depend on the
    # UV chart, so it is accumulated per position instead.
    contribs = {}
    normal_acc = {}
    corner_key = [None] * n_corners
    degenerate_tris = 0

    for ti in range(n_tris):
        tri = tris[ti]
        this_uv = (corner_uv[3 * ti + 0],
                   corner_uv[3 * ti + 1],
                   corner_uv[3 * ti + 2])
        canon, offset = _canonical_triangle(this_uv, tri)

        (a, uv0), (b, uv1), (c, uv2) = canon
        p0, p1, p2 = pos[a], pos[b], pos[c]

        du1 = uv1[0] - uv0[0]
        dv1 = uv1[1] - uv0[1]
        du2 = uv2[0] - uv0[0]
        dv2 = uv2[1] - uv0[1]
        det = du1 * dv2 - dv1 * du2

        e1 = _sub(p1, p0)
        e2 = _sub(p2, p0)
        n_raw = _cross(e1, e2)

        len1 = math.hypot(du1, dv1)
        len2 = math.hypot(du2, dv2)
        degenerate = (len1 <= TINY or len2 <= TINY or
                      abs(det) <= UV_EPS * len1 * len2)

        if degenerate:
            degenerate_tris += 1
            h = 0
            t_raw = None
            w_uv = (0.0, 0.0, 0.0)
        else:
            inv = 1.0 / det
            h = 1 if det > 0.0 else -1
            t_raw = ((e1[0] * dv2 - e2[0] * dv1) * inv,
                     (e1[1] * dv2 - e2[1] * dv1) * inv,
                     (e1[2] * dv2 - e2[2] * dv1) * inv)
            # Mikktspace style angle weighting in UV space: removes the bias
            # introduced by the arbitrary split of a quad into two triangles.
            w_uv = _corner_angles(uv0, uv1, uv2)
        # The normal is accumulated with the geometric (position) angle
        # weights so that it stays correct on mirrored islands where the UV
        # chart is flipped: mirrored UV charts reverse the sign of the UV
        # angle weights relative to the tangent, position weights never do.
        w_pos = _corner_angles(p0, p1, p2)

        # Total order on the contribution so that summation is independent of
        # the input face order (floats are compared via their exact repr).
        sort_key = (tuple((vid, u, v) for vid, (u, v) in canon),
                    h, repr((n_raw, t_raw)))
        # Split key: (position, handedness).  Grouping by position alone keeps
        # the tangent smooth across a UV seam (both sides of the seam are the
        # same geometry and must not disagree about "which way is +u"), while
        # grouping by handedness keeps a position shared by a normal and a
        # mirrored chart in two separate groups so their tangents cannot
        # cancel each other out.
        for k, (vid, uv) in enumerate(canon):
            key = (vid, h)
            corner_key[3 * ti + ((offset + k) % 3)] = key
            contribs.setdefault(key, []).append(
                (sort_key, w_uv[k], w_pos[k], n_raw, t_raw))
            normal_acc.setdefault(vid, []).append(
                (sort_key, w_pos[k], n_raw))

    # ------------------------------------------------------------------
    # resolve each split group: sort -> sum -> Gram-Schmidt against normal
    # ------------------------------------------------------------------
    resolved = {}
    fallback_keys = 0
    counts = {}

    # Normals are resolved per position, independent of the UV chart.
    normals = {}
    for vid, items in normal_acc.items():
        items.sort(key=lambda item: item[0])
        s = [0.0, 0.0, 0.0]
        for _sk, w_pos, n_raw in items:
            s[0] += w_pos * n_raw[0]
            s[1] += w_pos * n_raw[1]
            s[2] += w_pos * n_raw[2]
        normals[vid] = _normalize(tuple(s))

    for key, items in contribs.items():
        vid = key[0]
        items.sort(key=lambda item: item[0])

        t_sum = [0.0, 0.0, 0.0]
        n_valid = 0
        n_degen = 0
        for _sk, w_uv, _w_pos, _n_raw, t_raw in items:
            if t_raw is None:
                n_degen += 1
            else:
                t_sum[0] += w_uv * t_raw[0]
                t_sum[1] += w_uv * t_raw[1]
                t_sum[2] += w_uv * t_raw[2]
                n_valid += 1
        counts[key] = (n_valid, n_degen)

        normal = normals[vid]
        tangent = None
        if normal is not None and n_valid > 0:
            proj = _dot(normal, tuple(t_sum))
            tangent = _normalize((t_sum[0] - normal[0] * proj,
                                  t_sum[1] - normal[1] * proj,
                                  t_sum[2] - normal[2] * proj))
        if tangent is None:
            # No usable tangent (every corner of this split group had zero UV
            # area, or the accumulated tangent collapsed onto the normal):
            # use the documented fallback basis around the vertex normal.
            tangent, _bt, fb_normal = _fallback_basis(
                (0.0, 0.0, 0.0) if normal is None else normal)
            normal = fb_normal
            fallback_keys += 1
        w = key[1] if key[1] else 1
        resolved[key] = (tangent, w, normal)

    # ------------------------------------------------------------------
    # per-corner output
    # ------------------------------------------------------------------
    tangents = []
    bitangent_sign = []
    corner_vertex = []
    fallback_corners = 0

    for ci in range(n_corners):
        key = corner_key[ci]
        tangent, w, _normal = resolved[key]
        tangents.append([tangent[0], tangent[1], tangent[2], float(w)])
        bitangent_sign.append(w)
        corner_vertex.append(key[0])
        if key[1] == 0:
            fallback_corners += 1

    # ------------------------------------------------------------------
    # canonical, face-order independent summary + digest
    # ------------------------------------------------------------------
    split_tangents = []
    for key in sorted(resolved):
        vid, h = key
        tangent, w, _normal = resolved[key]
        split_tangents.append([
            vid, h,
            _q(tangent[0]), _q(tangent[1]), _q(tangent[2]), w,
            counts[key][0], counts[key][1],
        ])
    payload = json.dumps(split_tangents, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return {
        "tangents": tangents,
        "bitangent_sign": bitangent_sign,
        "corner_vertex": corner_vertex,
        "split_vertices": len(resolved),
        "degenerate": degenerate_tris,
        "degenerate_triangles": degenerate_tris,
        "degenerate_corners": 3 * degenerate_tris,
        "fallback_keys": fallback_keys,
        "fallback_corners": fallback_corners,
        "uv_mode": uv_mode,
        "split_tangents": split_tangents,
        "digest": digest,
    }


def _q(x):
    """Round for the digest and fold -0.0 into 0.0 so ordering cannot leak."""
    y = round(float(x), DIGEST_DIGITS)
    return 0.0 if y == 0.0 else y


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        sys.stderr.write("usage: python3 tangent.py mesh.json output.json\n")
        return 2
    with open(argv[0], "r", encoding="utf-8") as fh:
        mesh = json.load(fh)
    with open(argv[1], "w", encoding="utf-8") as fh:
        json.dump(run(mesh), fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
