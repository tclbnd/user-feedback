#!/usr/bin/env python3
"""Generate synthetic test scenes (run: python3 make_scenes.py).

Scenes are written next to this script under scenes/.
"""
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "scenes")


def write(name, scene):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump(scene, f, indent=1)
    print("wrote", path)


def sphere_patch(u0, u1, v0=0.0, v1=1.0, r=1.0):
    return {"type": "sphere", "uv": [u0, v0, u1, v1],
            "center": [0.0, 0.0, 0.0], "r": r}


def saddle_patch(x0, x1, y0, y1):
    return {"type": "saddle", "uv": [x0, y0, x1, y1],
            "center": [0.0, 0.0, 0.0], "r": 1.0}


def main():
    # 1. closed sphere, 4 equal longitude bands, equal lat span
    n = 4
    bands = [sphere_patch(i / n, (i + 1) / n) for i in range(n)]
    write("sphere4.json", {"patches": bands, "err": 0.05,
                           "order": list(range(n))})

    # 2. single saddle rectangle with the central hole
    write("saddle_hole.json",
          {"patches": [saddle_patch(-1.0, 1.0, -1.0, 1.0)],
           "err": 0.05, "order": [0]})

    # 3. 2x2 saddle tiling; every patch carries its own center hole,
    #    shared edges at x=0 and y=0
    half = 1.0
    rects = [(-half, 0, -half, 0), (0, half, -half, 0),
             (-half, 0, 0, half), (0, half, 0, half)]
    tiles = [saddle_patch(*r) for r in rects]
    write("saddle_grid.json", {"patches": tiles, "err": 0.05,
                               "order": [0, 1, 2, 3]})

    # 4. mixed-width sphere bands: widths 1/16, 3/16, 3/16, 9/16
    #    -> required densities differ by ~9x (>= 4:1 level difference
    #       after power-of-two negotiation)
    widths = [1 / 16, 3 / 16, 3 / 16, 9 / 16]
    acc, mixed = 0.0, []
    for w in widths:
        mixed.append(sphere_patch(acc, acc + w))
        acc += w
    write("mixed_sphere.json", {"patches": mixed, "err": 0.05,
                                "order": [0, 1, 2, 3]})

    # 5. sphere bands with reversed parameter spans (u0 > u1) and a
    #    shuffled processing order - orientation/order stress test
    rev = [sphere_patch(0.5, 0.25), sphere_patch(0.25, 0.0),
           sphere_patch(1.0, 0.75), sphere_patch(0.75, 0.5)]
    rng = random.Random(20261114)
    rorder = list(range(4))
    rng.shuffle(rorder)
    write("reversed_sphere.json", {"patches": rev, "err": 0.05,
                                   "order": rorder})


if __name__ == "__main__":
    main()
