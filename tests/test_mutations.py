#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation test: prove the acceptance suite actually catches the defects.

The task statement names two defects in the baseline renderer:

  1. painter's algorithm in *input* order instead of depth order;
  2. the Gaussian hard-clipped at the tile that owns the primitive;
  3. depth-only sorting, leaving the order of equal-depth primitives to the
     input order (stable sort, so a shuffle changes the frame).

Each mutation below re-implements exactly one of those errors on top of the
correct implementation and asserts that the acceptance suite names in
tests/selftest.py fails it.  A suite that passed the mutants would be vacuous.

Run:  python3 tests/test_mutations.py
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import selftest  # noqa: E402


def mutated_render(order_by_input=False, clip_to_tile=False,
                   depth_only_sort=False):
    """Return a render function with the requested defect(s) injected."""

    def render(req):
        W, H = int(req["w"]), int(req["h"])
        T = int(req.get("tile", 8))
        prims = []
        for raw in req["prims"]:
            p = selftest.splat._make_primitive(raw)
            if p is not None:
                prims.append(p)
        if not order_by_input:
            if depth_only_sort:
                # DEFECT: ties in depth are broken by the incoming order
                prims.sort(key=lambda p: p["z"])
            else:
                prims.sort(key=selftest.splat._order_key)

        ntx = (W + T - 1) // T
        nty = (H + T - 1) // T
        tiles = [[[] for _ in range(ntx)] for _ in range(nty)]
        for pi, p in enumerate(prims):
            x, y, rad = p["x"], p["y"], p["rad"]
            if clip_to_tile:
                # DEFECT: assign the primitive to the single tile containing
                # its centre.  Everything the kernel spills into the
                # neighbouring tile is then lost -> a hard band cut at the
                # tile boundary that is absent from the true image.
                cx = min(ntx - 1, max(0, int(x // T)))
                cy = min(nty - 1, max(0, int(y // T)))
                tiles[cy][cx].append(pi)
                continue
            tx0 = max(0, int((x - rad) // T))
            tx1 = min(ntx - 1, int((x + rad) // T))
            ty0 = max(0, int((y - rad) // T))
            ty1 = min(nty - 1, int((y + rad) // T))
            p["px1"] = min(W - 1, p["px1"])
            p["py1"] = min(H - 1, p["py1"])
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    tiles[ty][tx].append(pi)

        n = W * H
        out_r = [0.0] * n
        out_g = [0.0] * n
        out_b = [0.0] * n
        trans = [1.0] * n
        pc = [0] * n

        for ty in range(nty):
            y0, y1 = ty * T, min(H, (ty + 1) * T)
            for tx in range(ntx):
                x0, x1 = tx * T, min(W, (tx + 1) * T)
                for pi in tiles[ty][tx]:
                    p = prims[pi]
                    if clip_to_tile:
                        # DEFECT: evaluate only inside the owning tile, so
                        # the kernel is cut where the tile ends
                        px0, px1 = x0, x1 - 1
                        py0, py1 = y0, y1 - 1
                    else:
                        px0 = max(x0, p["px0"])
                        px1 = min(x1 - 1, p["px1"])
                        py0 = max(y0, p["py0"])
                        py1 = min(y1 - 1, p["py1"])
                    if px0 > px1 or py0 > py1:
                        continue
                    cx, cy = p["x"], p["y"]
                    ixx, ixy, iyy = p["ixx"], p["ixy"], p["iyy"]
                    a, cr, cg, cb = p["a"], p["r"], p["g"], p["b"]
                    for yy in range(py0, py1 + 1):
                        dy = yy + 0.5 - cy
                        off = yy * W
                        for xx in range(px0, px1 + 1):
                            dx = xx + 0.5 - cx
                            g = selftest.math.exp(
                                -0.5 * (ixx * dx * dx + 2 * ixy * dx * dy
                                        + iyy * dy * dy))
                            aa = a * g
                            idx = off + xx
                            t = trans[idx]
                            out_r[idx] += cr * aa * t
                            out_g[idx] += cg * aa * t
                            out_b[idx] += cb * aa * t
                            trans[idx] = t * (1 - aa)
                            pc[idx] += 1

        pixels = [[out_r[i], out_g[i], out_b[i]] for i in range(n)]
        digest = selftest.splat.hashlib.sha1(
            repr(pixels).encode()).hexdigest()
        return {"pixels": pixels, "tile_counts": [], "pixel_counts": pc,
                "digest": digest}

    return render


def expect_failure(label, render, scenes):
    """Assert that the acceptance checks reject this renderer."""
    failures = 0
    details = []
    for name, req in scenes:
        acc = selftest.check_accuracy(name, req, renderer=render)
        seam = selftest.check_seams(name, req, renderer=render)
        inv = selftest.check_invariance(name, req, renderer=render)
        tied = selftest.check_equal_depth(name, req, renderer=render)
        for failed, tag in ((acc, "accuracy"), (seam, "seam"),
                           (inv, "invariance"), (tied, "equal-depth")):
            if not failed:
                failures += 1
                details.append(tag)
    print(f"  -> {label}: {failures} failed checks, "
          f"caught_by={sorted(set(details))}")
    return failures > 0


def main():
    # build a scene set: one detailed, one flat-gradient, one with tied z
    scenes = [
        ("mut-random", selftest.make_scene(
            selftest.random.Random(31), 32, 32, 24)),
        ("mut-tied", selftest.make_scene(
            selftest.random.Random(35), 32, 32, 24, z_levels=[1.0, 2.0])),
    ]

    ok = True
    for label, opts in (("input-order painter", dict(order_by_input=True)),
                        ("tile-edge clipping", dict(clip_to_tile=True)),
                        ("depth-only sort (unstable ties)",
                         dict(depth_only_sort=True)),
                        ("both baseline defects",
                         dict(order_by_input=True, clip_to_tile=True))):
        if not expect_failure(label, mutated_render(**opts), scenes):
            ok = False

    print("\n" + ("MUTANTS KILLED - suite is non-vacuous" if ok
                  else "SOME MUTANTS SURVIVED - suite is too weak"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
