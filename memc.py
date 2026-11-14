#!/usr/bin/env python3
"""Motion-compensated frame interpolation (linear-blend baseline)."""
import json, sys, os

def read_pgm(p):
    t = open(p).read().split()
    assert t[0] == "P2"
    w, h, mx = int(t[1]), int(t[2]), int(t[3])
    px = list(map(int, t[4:4 + w * h]))
    return w, h, [px[i * w:(i + 1) * w] for i in range(h)]

def write_pgm(p, w, h, img):
    with open(p, "w") as f:
        f.write(f"P2\n{w} {h}\n255\n")
        for row in img:
            f.write(" ".join(map(str, row)) + "\n")

def blend(i0, i1, t):
    return [[round((1 - t) * a + t * b) for a, b in zip(r0, r1)]
            for r0, r1 in zip(i0, i1)]

def main():
    req = json.load(open(sys.argv[1]))
    outdir = sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    frames = []
    for fr in req["frames"]:
        frames.append(read_pgm(os.path.join(os.path.dirname(sys.argv[1]), fr["file"])))
    (w0, h0, i0), (w1, h1, i1) = frames[0], frames[1]
    for t in req["t"]:
        img = blend(i0, i1, t)
        write_pgm(os.path.join(outdir, f"interp_{t}.pgm"), w0, h0, img)
    json.dump({"occl_ratio": 0.0, "fallback_px": 0, "mv_count": 0},
              open(os.path.join(outdir, "stats.json"), "w"))

if __name__ == "__main__":
    main()
