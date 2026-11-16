"""Randomised differential check: geo.py vs. the independent reference.

Usage:
    python3 tests/property_check.py [ITERATIONS] [SEED]

Exits non-zero if any randomly generated frame disagrees with the reference
implementation in tests/test_geo.py.  This is a development aid; the unittest
suite is the authoritative acceptance check.
"""

from __future__ import annotations

import os
import random
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))

import geo  # noqa: E402
import fixtures  # noqa: E402
import test_geo  # noqa: E402

TRANSFORMS = [None, "r90", "r180", "r270", "mirror", "vflip",
              "r90+mirror", "r270+vflip", "mirror+r90"]
STRATEGIES = ["fit", "fill", "stretch", "none"]
SARS = [(1, 1), (4, 3), (3, 4), (64, 45), (32, 27), (10, 11), (2, 1), (1, 2)]


def random_case(rng):
    coded_w = rng.choice([2, 3, 7, 100, 720, 1919, 1920, 1921, 4096])
    coded_h = rng.choice([2, 3, 5, 99, 576, 1079, 1080, 1081, 2160])
    ax = rng.choice([0, 1, 2, 3, 5, 11, 120])
    ay = rng.choice([0, 1, 3, 7, 13, 30])
    aw = rng.choice([1, 2, 6, 99, 700, 1079, 1200, coded_w,
                     max(1, coded_w - ax)])
    ah = rng.choice([1, 2, 4, 98, 400, 575, 720, coded_h,
                     max(1, coded_h - ay)])
    canvas = (rng.choice([1, 3, 100, 721, 1080, 1281, 1919, 1920]),
              rng.choice([1, 2, 100, 481, 721, 1080, 1081, 2160]))
    return (coded_w, coded_h, ax, ay, aw, ah, rng.choice(SARS),
            rng.choice(TRANSFORMS), canvas, rng.choice(STRATEGIES))


def main(argv):
    iterations = int(argv[0]) if argv else 4000
    seed = int(argv[1]) if len(argv) > 1 else 7
    rng = random.Random(seed)
    mismatches = 0
    for _ in range(iterations):
        coded_w, coded_h, ax, ay, aw, ah, sar, transform, canvas, strategy = \
            random_case(rng)
        frame = fixtures.frame("x", coded=(coded_w, coded_h),
                               aperture=(ax, ay, aw, ah), sar=sar,
                               transform=transform)
        payload = fixtures.payload([frame], canvas=canvas, strategy=strategy)
        got = geo.map_payload(payload)["frames"][0]
        want = test_geo.reference(frame, canvas, strategy)
        if got != want:
            mismatches += 1
            if mismatches <= 5:
                print("MISMATCH", (coded_w, coded_h, ax, ay, aw, ah, sar,
                                   transform, canvas, strategy))
                print("  got ", got)
                print("  want", want)
    print(f"{iterations} cases, {mismatches} mismatches")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
