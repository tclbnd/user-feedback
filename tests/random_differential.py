"""Randomized differential testing: tiled scanliner vs independent winding.

Fixed seed for reproducibility. Random paths mix line and quadratic segments,
self-intersection, off-canvas coordinates and duplicated points; every scene is
rendered under both fill rules, several tile sizes (including 1 and a tile
larger than the image) and both rasterizers. All masks must agree bit for bit.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rsample.coverage import render, rasterize_reference, rasterize_tiled

TILES = [1, 2, 3, 7, 64]
W, H = 9, 7


def random_path(rng):
    cmds = [["M", rng.randint(-2, W + 1), rng.randint(-2, H + 1)]]
    for _ in range(rng.randint(3, 9)):
        kind = rng.choice(("L", "L", "L", "Q"))
        if rng.random() < 0.2:  # deliberately repeat a point / control point
            cmds.append(["L", cmds[-1][-2], cmds[-1][-1]])
            continue
        if kind == "L":
            cmds.append(["L", rng.randint(-2, W + 1), rng.randint(-2, H + 1)])
        else:
            cmds.append(["Q",
                         rng.randint(-2, W + 1), rng.randint(-2, H + 1),
                         rng.randint(-2, W + 1), rng.randint(-2, H + 1)])
    if rng.random() < 0.7:
        cmds.append(["Z"])
    return cmds


def main():
    rng = random.Random(20261113)
    failures = 0
    for case in range(80):
        rule = rng.choice(("nonzero", "evenodd"))
        clips = []
        if rng.random() < 0.5:
            clips.append(dict(rule=rng.choice(("nonzero", "evenodd")),
                              commands=random_path(rng)))
        scene = dict(size=[W, H],
                     shape=dict(rule=rule, commands=random_path(rng)),
                     clips=clips,
                     color=[0.3, 0.5, 0.2, 0.7])
        ref = render(scene, rasterizer=rasterize_reference)["mask"]
        first = None
        for tile in TILES:
            got = render(scene, tile=tile, rasterizer=rasterize_tiled)["mask"]
            if got != ref:
                failures += 1
                print("FAIL case", case, "tile", tile, "rule", rule)
                break
            if first is None:
                first = got
            elif got != first:
                failures += 1
                print("FAIL tile-invariance case", case, "tile", tile)
                break
        else:
            continue
        if failures >= 3:
            break
    print("random differential:", "ALL OK" if failures == 0
          else ("%d FAILURES" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
