#!/usr/bin/env python3
import json, sys, math

def gaussians(seed, n):
    out = []; x = seed; i = 0
    while len(out) < n:
        x = (x * 1103515245 + 12345) % 2**31
        u1 = max(x / 2**31, 1e-12)
        x = (x * 1103515245 + 12345) % 2**31
        u2 = x / 2**31
        out.append(math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2))
        if len(out) < n:
            out.append(math.sqrt(-2 * math.log(u1)) * math.sin(2 * math.pi * u2))
    return out[:n]

def main():
    d = json.load(open(sys.argv[1]))
    L = d["luma"]; P = d["params"]
    H, W = len(L), len(L[0])
    nb = ((H + 7) // 8) * ((W + 7) // 8)
    gs = gaussians(P["seed"], nb)
    dc = []; prev = 0.0
    for g in gs:
        prev = 0.8 * prev + 0.6 * g; dc.append(prev)
    A, Q = P["scale"], P["strength"]
    bi = 0; out = []
    for r0 in range(0, H, 8):
        for c0 in range(0, W, 8):
            for y in range(r0, min(r0 + 8, H)):
                for x in range(c0, min(c0 + 8, W)):
                    idx = (y // 8) * ((W + 7) // 8) + (x // 8)
                    gr = dc[idx] * A * min(1, L[y][x] / 255 * Q / 10)
                    while len(out) <= y:
                        out.append([0] * W)
                    out[y][x] = max(0, min(255, round(L[y][x] + gr)))
    json.dump({"luma": out, "dc": [round(v, 4) for v in dc]},
              open(sys.argv[2], "w"), indent=1)

main()
