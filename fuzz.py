#!/usr/bin/env python3
"""Randomised cross-check: many random scenes / leaf caps / ray sets, each
compared against a brute-force scan, plus shuffled-input invariance.

    python3 fuzz.py [iterations]
"""

import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bvh  # noqa: E402


def bf(o, d, t):
    a, b, c = t
    e1 = [b[i] - a[i] for i in range(3)]
    e2 = [c[i] - a[i] for i in range(3)]
    p = [d[1] * e2[2] - d[2] * e2[1],
         d[2] * e2[0] - d[0] * e2[2],
         d[0] * e2[1] - d[1] * e2[0]]
    det = sum(e1[i] * p[i] for i in range(3))
    if abs(det) < 1e-12:
        return None
    inv = 1.0 / det
    s = [o[i] - a[i] for i in range(3)]
    u = sum(s[i] * p[i] for i in range(3)) * inv
    if u < 0 or u > 1:
        return None
    q = [s[1] * e1[2] - s[2] * e1[1],
         s[2] * e1[0] - s[0] * e1[2],
         s[0] * e1[1] - s[1] * e1[0]]
    v = sum(d[i] * q[i] for i in range(3)) * inv
    if v < 0 or u + v > 1:
        return None
    return sum(e2[i] * q[i] for i in range(3)) * inv


def random_tri(rng, scale):
    cx, cy, cz = (rng.uniform(-scale, scale) for _ in range(3))
    h = 10 ** rng.uniform(-3, -0.5) * scale
    return [[cx - h, cy - h, cz], [cx + h, cy - h, cz], [cx, cy + h, cz]]


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rng = random.Random(20240518)
    checked = 0
    for it in range(iters):
        n = rng.choice([0, 1, 2, 3, 5, 9, 17, 40, 120, 400])
        scale = 10 ** rng.uniform(-2, 2)
        mode = rng.choice(["random", "line", "plane", "cluster", "dups"])
        if mode == "line":
            tris = [[[x, 0.0, 0.0], [x + 1e-3, 0.0, 0.0], [x, 1e-3, 0.0]]
                    for x in (rng.uniform(-5, 5) for _ in range(n))]
        elif mode == "plane":
            tris = [[[rng.uniform(-1, 1), rng.uniform(-1, 1), 0.0],
                     [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]] for _ in range(n)]
        elif mode == "cluster":
            c = [rng.uniform(-1, 1) for _ in range(3)]
            tris = [[[c[0] + rng.gauss(0, 1e-3), c[1], c[2]],
                     [c[0], c[1] + 1e-3, c[2]],
                     [c[0], c[1], c[2] + 1e-3]] for _ in range(n)]
        elif mode == "dups":
            base = [random_tri(rng, scale) for _ in range(max(1, n // 4))]
            tris = [base[rng.randrange(len(base))] for _ in range(n)]
        else:
            tris = [random_tri(rng, scale) for _ in range(n)]

        leaf = rng.choice([1, 2, 3, 4, 8, 16, 64])
        rays = []
        for _ in range(30):
            o = [rng.uniform(-3 * scale, 3 * scale) for _ in range(3)]
            if tris and rng.random() < 0.6:
                t = tris[rng.randrange(len(tris))]
                u = rng.random()
                v = rng.random() * (1 - u)
                p = [t[0][i] * (1 - u - v) + t[1][i] * u + t[2][i] * v
                     for i in range(3)]
            else:
                p = [rng.uniform(-scale, scale) for _ in range(3)]
            d = [p[i] - o[i] for i in range(3)]
            ln = math.sqrt(sum(x * x for x in d))
            if ln == 0.0:
                d = [0.0, 0.0, 1.0]
            else:
                d = [x / ln for x in d]
            rays.append({"o": o, "d": d})

        res = bvh.run({"tris": tris, "rays": rays, "leaf": leaf})
        for j, h in enumerate(res["hits"]):
            best = None
            for t in tris:
                hh = bf(rays[j]["o"], rays[j]["d"], t)
                if hh is not None and 1e-9 < hh < 1e30 and \
                        (best is None or hh < best):
                    best = hh
            if best is None:
                assert h["tri"] == -1 and h["t"] == -1.0, \
                    "iter %d ray %d: false hit" % (it, j)
            else:
                assert h["tri"] >= 0, "iter %d ray %d: false miss" % (it, j)
                assert h["t"] == best, \
                    "iter %d ray %d: %r != %r" % (it, j, h["t"], best)
            checked += 1
        assert 0 <= res["stats"]["max_leaf"] <= leaf, "leaf cap violated"

        # Reordering must not change hits or the summary.
        perm = list(range(len(tris)))
        rng.shuffle(perm)
        res2 = bvh.run({"tris": [tris[i] for i in perm], "rays": rays,
                        "leaf": leaf})
        assert res2["digest"] == res["digest"], "iter %d: digest changed" % it
        assert res2["hits"] == res["hits"], \
            "iter %d: hits changed under shuffle" % it
        assert res2["height"] == res["height"] and \
            res2["nodes"] == res["nodes"] and \
            res2["sah_cost"] == res["sah_cost"], \
            "iter %d: tree summary changed under shuffle" % it

    print("fuzz: %d iterations, %d ray checks, all exact" % (iters, checked))


if __name__ == "__main__":
    main()
