#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    w, h, B, S, P = d["w"], d["h"], d["block"], d["strength"], d["protect"]
    px = [row[:] for row in d["pixels"]]
    edges = []; changed = []
    for x0 in range(B, w, B):                    # vertical edges
        A = abs(sum(px[y][x0-1]+px[y][x0-2] for y in range(h))
                - sum(px[y][x0]+px[y][x0+1] for y in range(h))) / (2*h)
        edges.append({"at": x0, "dir": "v", "A": round(A, 1)})
        if A < S:
            continue
        for y in range(h):                       # BUG 1: 不过 protect 梯度
            for dx, across in ((-1, x0), (0, x0-1), (-2, x0), (1, x0-2)):
                xi = x0 + dx
                a = px[y][xi]; b = px[y][across]
                wgt = 4 if abs(dx) <= 1 else 4   # BUG 2: 远邻权重不区分
                nv = (3 * a + b + 2) // 4
                if nv != a:
                    px[y][xi] = nv; changed.append([xi, y])
    for y0 in range(B, h, B):
        A = abs(sum(px[y0-1][x]+px[y0-2][x] for x in range(w))
                - sum(px[y0][x]+px[y0+1][x] for x in range(w))) / (2*w)
        edges.append({"at": y0, "dir": "h", "A": round(A, 1)})
        if A < S:
            continue
        for x in range(w):
            for dy, across in ((-1, y0), (0, y0-1), (-2, y0), (1, y0-2)):
                yi = y0 + dy
                a = px[yi][x]; b = px[across][x]
                nv = (3 * a + b + 2) // 4
                if nv != a:
                    px[yi][x] = nv; changed.append([x, yi])
    changed.sort(key=lambda c: (c[1], c[0]))
    json.dump({"pixels": px, "filtered": changed, "edges": edges},
              open(sys.argv[2], "w"), indent=1)

main()
