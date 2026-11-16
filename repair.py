#!/usr/bin/env python3
import json, sys, math

def main():
    d = json.load(open(sys.argv[1]))
    K = d["order"]; out = []
    for f in d["frames"]:
        m = list(f["mags"]); mask = f["mask"]; n = len(m)
        i = 0
        while i < n:
            if not mask[i]:
                i += 1; continue
            a = i
            while i < n and mask[i]:
                i += 1
            b = i - 1
            left = m[a - 1] if a > 0 else None
            right = m[b + 1] if b + 1 < n else None
            if left is not None and right is not None:
                # BUG 1: 线性幅度域插值（应为 log 域）
                for k in range(a, b + 1):
                    m[k] = left + (right - left) * (k - (a - 1)) / ((b + 1) - (a - 1))
            elif left is not None:
                for k in range(a, b + 1):
                    m[k] = left
            elif right is not None:
                for k in range(a, b + 1):
                    m[k] = right
        # BUG 2: 平滑施加到所有 bin（应只修过掩码位）
        sm = [0.0] * n
        for i in range(n):
            tot = 0.0
            for k in range(i - K, i + K + 1):
                tot += m[k] if 0 <= k < n else 0.0
            sm[i] = tot / (2 * K + 1)
        m = sm
        out.append({"t": f["t"], "mags": [round(v, 5) for v in m]})
    json.dump({"frames": out}, open(sys.argv[2], "w"), indent=1)

main()
