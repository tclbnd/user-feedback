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
                # FIX 1: interpolate in LOG magnitude domain
                # (clamp context at a tiny floor so a zero-magnitude
                # context bin cannot break the log)
                l = math.log(left) if left > 0 else math.log(1e-12)
                r = math.log(right) if right > 0 else math.log(1e-12)
                for k in range(a, b + 1):
                    m[k] = math.exp(l + (r - l) * (k - (a - 1)) / ((b + 1) - (a - 1)))
            elif left is not None:
                for k in range(a, b + 1):
                    m[k] = left
            elif right is not None:
                for k in range(a, b + 1):
                    m[k] = right
            else:
                for k in range(a, b + 1):
                    m[k] = 0.0
        # FIX 2: smoothing applies ONLY to previously-masked bins;
        # unmasked bins keep their original values. The moving average
        # is computed from the repaired spectrum (snapshot), not in place.
        sm = [0.0] * n
        for j in range(n):
            tot = 0.0
            for k in range(j - K, j + K + 1):
                tot += m[k] if 0 <= k < n else 0.0
            sm[j] = tot / (2 * K + 1)
        for j in range(n):
            if mask[j]:
                m[j] = sm[j]
        out.append({"t": f["t"], "mags": [round(v, 5) for v in m]})
    json.dump({"frames": out}, open(sys.argv[2], "w"), indent=1)

main()
