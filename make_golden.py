#!/usr/bin/env python3
"""Build the acceptance fixtures and their fixed reference outputs.

Synthetic clips (32x32, 8x8 transform blocks unless noted):

  checkerboard  - 2x2 luma checkerboard: every grid edge is a real contour
  step          - smooth ramp with a true hard step at x=11 (off-grid) plus
                  a pure block artefact step on the x=24 grid line
  diagonal      - diagonal stripe texture crossing every grid boundary
  hard_edge     - flat regions with blocking artefacts and a preserved
                  bright/dark hard contour on the x=16 grid line

Extra fixtures exercise the image outer edge, unprocessable regions,
12-bit saturation, the vertical-before-horizontal schedule and input
immutability.  Run: python3 make_golden.py   (writes golden/*.json)
"""
import json
import os

import deblock

W = H = 32
B = 8
QP = 28
HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")


def checkerboard_clip():
    px = [[128 if ((x // 2) + (y // 2)) % 2 == 0 else 24
           for x in range(W)] for y in range(H)]
    return {"name": "checkerboard", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": px}


def step_clip():
    px = []
    for y in range(H):
        row = []
        for x in range(W):
            v = 60 + 2 * x                      # smooth ramp
            if x >= 11:                         # true hard step (off-grid)
                v += 110
            if x >= 24:                         # pure block artefact line
                v = row[23] + 14
            row.append(v)
        px.append(row)
    return {"name": "step", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": px}


def diagonal_clip():
    px = [[(140 if (x + y) % 4 < 2 else 96) for x in range(W)]
          for y in range(H)]
    return {"name": "diagonal", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": px}


def hard_edge_clip():
    # Flat 80/200 regions meeting on the x=16 grid line = real hard edge.
    # A +20 step on the x=24 grid line is a pure block artefact, and a
    # horizontal +20 band at y=8 adds another blocking boundary.
    px = []
    for y in range(H):
        row = []
        for x in range(W):
            v = 80 if x < 16 else 200
            if x >= 24:
                v += 20                          # block artefact, not content
            if y == 8:
                v += 20                          # horizontal block artefact
            row.append(v)
        px.append(row)
    return {"name": "hard_edge", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": px}


def mask_clip():
    base = [[100 + 20 * (x // B) for x in range(W)] for _ in range(H)]
    mask = [[1 if 6 <= x <= 9 else 0 for x in range(W)] for _ in range(H)]
    return {"name": "mask", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": base, "mask": mask}


def bitdepth_clip():
    px = [[4072 if x < 16 else 4095 for x in range(W)] for _ in range(16)]
    return {"name": "bitdepth12", "w": W, "h": 16, "block": B,
            "qp": 6, "bitdepth": 12, "pixels": px}


def cross_clip():
    # Identical +20 step on both a vertical and a horizontal grid boundary.
    px = [[100 if (x < 16 and y < 16) else 120 for x in range(W)]
          for y in range(H)]
    return {"name": "cross_schedule", "w": W, "h": H, "block": B,
            "qp": QP, "pixels": px}


def outer_clip():
    # block size 4 keeps the 2-pixel stencil inside the frame; the 16-wide
    # frame has no grid edge colliding with the image border.
    px = [[100 + 20 * (x // 4) for x in range(16)] for _ in range(8)]
    return {"name": "outer", "w": 16, "h": 8, "block": 4,
            "qp": QP, "pixels": px}


def outer_edge_clip():
    # B == 1 puts a grid boundary at the image border, where the 2-pixel
    # stencil cannot be formed: it must be classified "outer" and skipped.
    px = [[100 + 5 * x for x in range(8)] for _ in range(4)]
    return {"name": "outer_edge", "w": 8, "h": 4, "block": 1,
            "qp": QP, "pixels": px}


def main():
    os.makedirs(GOLDEN, exist_ok=True)
    fixtures = [checkerboard_clip(), step_clip(), diagonal_clip(),
                hard_edge_clip(), mask_clip(), bitdepth_clip(),
                cross_clip(), outer_clip(), outer_edge_clip()]
    for fx in fixtures:
        name = fx.pop("name")
        with open(os.path.join(GOLDEN, name + ".in.json"), "w") as f:
            json.dump(fx, f, indent=1)
        result = deblock.deblock(fx)
        with open(os.path.join(GOLDEN, name + ".ref.json"), "w") as f:
            json.dump(result, f, indent=1)
        tags = sorted(set(e["class"] for e in result["edges"]))
        print(name, "edges:",
              [(e["dir"], e["at"], e["class"], e["filtered"])
               for e in result["edges"]])
        print("  classes:", tags, "changed:", len(result["filtered"]))


if __name__ == "__main__":
    main()
