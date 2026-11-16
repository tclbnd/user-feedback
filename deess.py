#!/usr/bin/env python3
import json, sys, math

def main():
    d = json.load(open(sys.argv[1]))
    T, R = d["threshold"], d["ratio"]
    ka = 1 - math.exp(-10 / d["attack"]); kr = 1 - math.exp(-10 / d["release"])
    g = 1.0; out = []; act = 0
    for f in d["frames"]:
        det = f["high"] / max(f["low"], 1e-9)
        if f["high"] == 0 and f["low"] == 0:
            det = 0.0
        over = det - T if det > T else 0
        want = max(1.0 / R, 1 - over * R)
        # BUG 1: 上升沿也用 attack 系数（应走 release）
        k = ka if want < g else ka
        g = g + (want - g) * k
        # BUG 2: active 判定用 want 而非平滑后 gain
        if want < 0.99:
            act += 1
        out.append({"t": f["t"], "det": round(det, 3), "gain": round(g, 4)})
    json.dump({"frames": out, "active_frames": act},
              open(sys.argv[2], "w"), indent=1)

main()
