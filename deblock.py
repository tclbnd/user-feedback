#!/usr/bin/env python3
"""Adaptive deblocking post-filter for block-transform video.

Fixes the boundary classification, vertical/horizontal filter scheduling and
frame post-processing of the original snapshot:

  * strength is selected from the fixture-supplied QP, the transform block
    size (block edge spacing) and the local sample difference (boundary
    activity A plus the inward texture gradient T);
  * every transform boundary is classified in one ordered sweep as
    "artifact" (blocking), "hard" (real bright/dark contour), "flat",
    "outer" (image outer edge) or "blocked" (unprocessable mask region);
  * vertical edges are filtered left->right, then horizontal edges top->bottom;
  * all samples are read from the *original* frame and written to a separate
    output buffer, so the input object is never mutated;
  * outputs are saturated to [0, 2**bitdepth - 1] (8-bit by default);
  * a per-boundary filtered flag (plus class and chosen strength) is exported.

Usage: python3 deblock.py input.json output.json
"""
import json
import math
import sys


def _half_up(x):
    """Deterministic round-half-up for non-negative values."""
    return int(math.floor(x + 0.5))


def deblock(frame):
    w = int(frame["w"])
    h = int(frame["h"])
    B = int(frame["block"])
    src = frame["pixels"]
    depth = int(frame.get("bitdepth", 8))
    maxval = (1 << depth) - 1
    qp = frame.get("qp")
    strength = frame.get("strength")
    protect = frame.get("protect")
    # 1 marks an unprocessable sample; "invalid" is accepted as an alias.
    mask = frame.get("mask")
    if mask is None:
        mask = frame.get("invalid")

    def masked(x, y):
        return mask is not None and mask[y][x] != 0

    # ------------------------------------------------------------------
    # Strength selection: QP -> quantiser step, scaled by block size and
    # bit depth.  t_lo is the artefact floor, t_hi the hard-edge cut-off.
    # ------------------------------------------------------------------
    if qp is not None:
        qstep = (2.0 ** ((float(qp) - 4.0) / 6.0)) * \
                float(1 << max(0, depth - 8))
        bscale = max(1.0, float(B) / 8.0)
        t_hi = max(1, _half_up(3.0 * qstep * bscale))
        t_lo_base = max(1, _half_up(0.5 * qstep * bscale))
        if strength is not None:
            t_lo_base = max(t_lo_base, int(strength))
        if protect is None:
            protect = max(2, _half_up(2.0 * qstep * bscale))
    else:
        # Legacy fixtures without QP: strength is the artefact floor and
        # there is no QP-derived hard cut-off.
        t_hi = None
        t_lo_base = int(strength) if strength is not None else 1
        if protect is None:
            protect = t_lo_base * 2
    protect = int(protect)

    out = [row[:] for row in src]
    done = set()          # (x, y) already filtered, first writer wins
    changed = []
    edges = []

    def saturate(v):
        if v < 0:
            return 0
        if v > maxval:
            return maxval
        return v

    def process(direction, at):
        vertical = direction == "v"
        span = w if vertical else h
        # 2-pixel stencil on each side of the edge.
        if at <= 0 or at >= span or at - 2 < 0 or at + 1 >= span:
            edges.append({"at": at, "dir": direction, "A": 0.0,
                          "class": "outer", "strength": t_lo_base,
                          "filtered": False})
            return

        def val(u, v):
            return src[u][v] if vertical else src[v][u]

        def is_masked(u, v):
            x, y = (v, u) if vertical else (u, v)
            return masked(x, y)

        # ---- boundary activity A and inward texture T (original samples)
        num = den = 0
        tnum = tden = 0
        usable_pairs = 0
        n_u = h if vertical else w
        for u in range(n_u):
            if is_masked(u, at - 1) or is_masked(u, at):
                continue
            usable_pairs += 1
            if not (is_masked(u, at - 2) or is_masked(u, at + 1)):
                num += (val(u, at - 1) + val(u, at - 2)
                        - val(u, at) - val(u, at + 1))
                den += 2
            tnum += abs(val(u, at - 1) - val(u, at - 2))
            tnum += abs(val(u, at + 1) - val(u, at))
            tden += 2

        entry = {"at": at, "dir": direction, "A": 0.0,
                 "class": None, "strength": t_lo_base, "filtered": False}

        if usable_pairs == 0:
            entry["class"] = "blocked"
            edges.append(entry)
            return

        A = abs(num) / den if den else 0.0
        texture = tnum / tden if tden else 0.0
        # Local sample difference raises the floor inside textured areas.
        thr = t_lo_base + _half_up(0.5 * texture)
        entry["A"] = round(A, 1)
        entry["strength"] = thr

        if A < thr:
            entry["class"] = "flat"
            edges.append(entry)
            return
        if t_hi is not None and A > t_hi:
            # Bright/dark content contour aligned with the grid: keep it.
            entry["class"] = "hard"
            edges.append(entry)
            return
        entry["class"] = "artifact"

        # (offset to edge, mirror neighbour offset, self weight,
        #  inward-neighbour offset for the protect gradient)
        layout = [
            (-1, at,     2, -2),   # p1, near: average with q1
            (0,  at - 1, 2,  1),   # q1, near: average with q1 mirror
            (-2, at + 1, 3, -3),   # p2, far : 3:1 blend with mirror q2
            (1,  at - 2, 3,  2),   # q2, far : 3:1 blend with mirror p2
        ]
        edge_changed = False
        for u in range(n_u):
            for off, across_v, wself, in_off in layout:
                v = at + off
                in_v = at + in_off
                if vertical:
                    x, y = v, u
                    xa, ya = across_v, u
                    xi, yi = in_v, u
                else:
                    x, y = u, v
                    xa, ya = u, across_v
                    xi, yi = u, in_v
                if not (0 <= x < w and 0 <= y < h):
                    continue                # image outer edge guard
                if not (0 <= xa < w and 0 <= ya < h):
                    continue
                if masked(x, y) or masked(xa, ya):
                    continue                # unprocessable region
                if (x, y) in done:
                    continue                # first writer wins (v then h)
                a = src[y][x]
                b = src[ya][xa]
                # Sample-level bright/dark hard-edge cut-off.
                if t_hi is not None and abs(a - b) > t_hi:
                    continue
                # BUG 1 fix: protect real texture via the inward gradient.
                if 0 <= xi < w and 0 <= yi < h and not masked(xi, yi):
                    if abs(a - src[yi][xi]) > protect:
                        continue
                # BUG 2 fix: near pixels 2:2, far pixels 3:1 (unbiased).
                nv = saturate(
                    (wself * a + (4 - wself) * b + 2) // 4)
                if nv != a:
                    out[y][x] = nv
                    done.add((x, y))
                    changed.append([x, y])
                    edge_changed = True
        entry["filtered"] = edge_changed
        edges.append(entry)

    # Specified schedule: vertical edges left->right, then horizontal top->bottom.
    for x0 in range(B, w, B):
        process("v", x0)
    for y0 in range(B, h, B):
        process("h", y0)

    changed.sort(key=lambda c: (c[1], c[0]))
    return {"pixels": out, "filtered": changed, "edges": edges}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 deblock.py input.json output.json")
    with open(sys.argv[1]) as f:
        frame = json.load(f)
    result = deblock(frame)
    with open(sys.argv[2], "w") as f:
        json.dump(result, f, indent=1)


if __name__ == "__main__":
    main()
