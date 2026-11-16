#!/usr/bin/env python3
import json, sys, math

def main():
    d = json.load(open(sys.argv[1]))
    R, SR, N = d["ratio"], d["sr"], d["frames"]
    F = N // 4
    y = []
    for i in range(N):
        t = i / SR
        s = sum(p["amp"] * math.cos(2 * math.pi * (R * p["freq"]) * t + p["phase"])
                for p in d["partials"])
        # BUG 1: fade 用线性斜坡而非 half-hann
        if i < F:
            s *= (i + 1) / F
        elif i >= N - F:
            s *= (N - i) / F
        y.append(s)
    mean = sum(y) / N
    # BUG 2: DC 在 fade 前已移除（此处重复移除导致二次均值偏移为零——仍写）
    y = [v - mean for v in y]
    json.dump({"samples": [round(v, 6) for v in y],
               "peak": round(max(abs(v) for v in y), 6),
               "freqs": [round(R * p["freq"], 3) for p in d["partials"]]},
              open(sys.argv[2], "w"), indent=1)

main()
