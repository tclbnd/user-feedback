#!/usr/bin/env python3
"""Generate synthetic translation + occlusion PGM pairs."""
import sys, os, random

def w(p, img):
    h, wd = len(img), len(img[0])
    with open(p, "w") as f:
        f.write(f"P2\n{wd} {h}\n255\n")
        for r in img:
            f.write(" ".join(map(str, r)) + "\n")

def scene_translate(wd, h, dx, seed=0):
    rng = random.Random(seed)
    bg = [[rng.randrange(40, 80) for _ in range(wd)] for _ in range(h)]
    box = [[220]*12 for _ in range(12)]
    f0 = [r[:] for r in bg]
    for y in range(12):
        for x in range(12):
            f0[10+y][20+x] = box[y][x]
    f1 = [r[:] for r in bg]
    for y in range(12):
        for x in range(12):
            f1[10+y][20+x+dx] = box[y][x]
    return f0, f1

def scene_occlusion(wd, h, seed=1):
    """前景块右移遮住背景特征: f1 中被遮区域应判遮挡。"""
    rng = random.Random(seed)
    f0 = [[rng.randrange(30, 60) for _ in range(wd)] for _ in range(h)]
    f1 = [r[:] for r in f0]
    for y in range(8, 24):
        for x in range(8, 24):
            f0[y][x] = 200                    # f0: 亮块在左
    for y in range(8, 24):
        for x in range(24, 40):
            f1[y][x] = 200                    # f1: 亮块在右, 中间区域被覆盖
    return f0, f1

if __name__ == "__main__":
    d = sys.argv[1]; os.makedirs(d, exist_ok=True)
    f0, f1 = scene_translate(96, 64, 10)
    w(os.path.join(d, "f0.pgm"), f0); w(os.path.join(d, "f1.pgm"), f1)
    g0, g1 = scene_occlusion(96, 64)
    w(os.path.join(d, "g0.pgm"), g0); w(os.path.join(d, "g1.pgm"), g1)
    print("wrote", d)
