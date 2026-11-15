#!/usr/bin/env python3
"""End-to-end CLI check: build the three required mesh families as JSON
files, run ``python3 bvh.py in.json out.json``, then verify the produced
report against an independent brute-force scan and against a shuffled copy
of the same triangle list.

    python3 check_cli.py
"""

import json
import math
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="bvhcli-", dir=os.environ.get("TMPDIR"))


def moller(o, d, t):
    a, b, c = t
    e1 = [b[i] - a[i] for i in range(3)]
    e2 = [c[i] - a[i] for i in range(3)]
    p = [d[1] * e2[2] - d[2] * e2[1],
         d[2] * e2[0] - d[0] * e2[2],
         d[0] * e2[1] - d[1] * e2[0]]
    det = sum(e1[i] * p[i] for i in range(3))
    if abs(det) < 1e-12:
        return None
    r = 1.0 / det
    s = [o[i] - a[i] for i in range(3)]
    u = sum(s[i] * p[i] for i in range(3)) * r
    if u < 0 or u > 1:
        return None
    q = [s[1] * e1[2] - s[2] * e1[1],
         s[2] * e1[0] - s[0] * e1[2],
         s[0] * e1[1] - s[1] * e1[0]]
    v = sum(d[i] * q[i] for i in range(3)) * r
    if v < 0 or u + v > 1:
        return None
    return sum(e2[i] * q[i] for i in range(3)) * r


def tri(cx, cy, cz, h):
    return [[cx - h, cy - h, cz], [cx + h, cy - h, cz], [cx, cy + h, cz]]


def build_scenes(rng):
    uniform = [tri(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1),
                   0.03) for _ in range(250)]
    strip = [tri(rng.uniform(-40, 40), rng.uniform(-0.2, 0.2),
                 rng.uniform(-0.2, 0.2), 0.02) for _ in range(250)]
    overlap = []
    for _ in range(250):
        c = [rng.choice([-0.15, 0.0, 0.15]) + rng.uniform(-0.02, 0.02)
             for _ in range(3)]
        overlap.append(tri(c[0], c[1], c[2], 0.12))
    return [("uniform", uniform), ("strip", strip), ("overlap", overlap)]


def rays_for(tris, rng, n=120):
    mn = [min(v[i] for t in tris for v in t) for i in range(3)]
    mx = [max(v[i] for t in tris for v in t) for i in range(3)]
    ctr = [(mn[i] + mx[i]) / 2 for i in range(3)]
    span = max(mx[i] - mn[i] for i in range(3))
    out = []
    for k in range(n):
        o = [ctr[i] + rng.uniform(-2.5, 2.5) * span for i in range(3)]
        if k % 2 == 0:
            t = tris[rng.randrange(len(tris))]
            u = rng.random()
            v = rng.random() * (1 - u)
            p = [t[0][i] * (1 - u - v) + t[1][i] * u + t[2][i] * v
                 for i in range(3)]
        else:
            p = [ctr[i] + rng.uniform(-0.5, 0.5) * span for i in range(3)]
        d = [p[i] - o[i] for i in range(3)]
        ln = math.sqrt(sum(x * x for x in d)) or 1.0
        out.append({"o": o, "d": [x / ln for x in d]})
    return out


def run(name, payload, tag):
    ip = os.path.join(TMP, "%s_%s_in.json" % (name, tag))
    op = os.path.join(TMP, "%s_%s_out.json" % (name, tag))
    with open(ip, "w") as f:
        json.dump(payload, f)
    subprocess.check_call([sys.executable, os.path.join(HERE, "bvh.py"), ip, op],
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    with open(op) as f:
        res = json.load(f)
    for key in ("hits", "height", "nodes", "sah_cost", "digest"):
        assert key in res, "missing %r in output" % key
    assert isinstance(res["hits"], list)
    assert len(res["hits"]) == len(payload["rays"])
    return res


def brute(tris, rays):
    out = []
    for r in rays:
        best = None
        for t in tris:
            h = moller(r["o"], r["d"], t)
            if h is not None and 1e-9 < h < 1e30 and (best is None or h < best):
                best = h
        out.append(best)
    return out


def main():
    rng = random.Random(20240501)
    ok = True
    for name, tris in build_scenes(rng):
        rays = rays_for(tris, rng)
        payload = {"tris": tris, "rays": rays, "leaf": 4, "seed": 1}
        res = run(name, payload, "a")

        ref = brute(tris, rays)
        max_err = 0.0
        for h, exp in zip(res["hits"], ref):
            if exp is None:
                assert h["tri"] == -1 and h["t"] == -1.0, "%s: false hit" % name
            else:
                assert h["tri"] >= 0, "%s: false miss" % name
                max_err = max(max_err, abs(h["t"] - exp))
                assert h["t"] == exp, "%s: distance mismatch %r vs %r" % (
                    name, h["t"], exp)

        # median baseline for the SAH gain report
        sys.path.insert(0, HERE)
        import bvh
        med = bvh.tree_cost(bvh.build(tris, 4, strategy="median"))
        ratio = res["sah_cost"] / med

        # shuffled triangle list must reproduce hits and summary
        order = list(range(len(tris)))
        rng.shuffle(order)
        res2 = run(name, {"tris": [tris[i] for i in order], "rays": rays,
                          "leaf": 4, "seed": 1}, "b")
        same = (res2["digest"] == res["digest"] and
                res2["height"] == res["height"] and
                res2["nodes"] == res["nodes"] and
                res2["sah_cost"] == res["sah_cost"] and
                res2["hits"] == res["hits"])
        ok = ok and same
        n_hit = sum(1 for h in res["hits"] if h["tri"] >= 0)
        print("%-8s rays=%d hits=%d max|dt|=%.1f height=%d nodes=%d "
              "sah=%.3f median=%.3f ratio=%.3f shuffled_identical=%s digest=%s"
              % (name, len(rays), n_hit, max_err, res["height"], res["nodes"],
                 res["sah_cost"], med, ratio, same, res["digest"][:12]))

    print("CLI OK" if ok else "CLI MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
