#!/usr/bin/env python3
import json, sys, math

def lp(x, fc, sr):
    a = 2 * math.pi * fc / sr / (1 + 2 * math.pi * fc / sr)
    y = []; p = 0.0
    for v in x:
        p = a * v + (1 - a) * p; y.append(p)
    return y

def hp(x, fc, sr):
    a = 2 * math.pi * fc / sr / (1 + 2 * math.pi * fc / sr)
    y = []; p = 0.0
    for v in x:
        p = a * v + (1 - a) * p; y.append(v - p)
    return y

def main():
    d = json.load(open(sys.argv[1]))
    sr = d["sr"]; bands = []
    for b in d["bands"]:
        lo, hi = b["range"]; x = d["signal"]
        if lo > 0:
            x = hp(x, lo, sr)
        if hi < sr / 2:
            x = lp(x, hi, sr)
        # BUG 1: 延迟左移而非右移
        sh = round(d["delay_ms"].get(b["driver"], 0) * sr / 1000)
        if sh:
            x = x[sh:] + [0.0] * sh
        bands.append({"driver": b["driver"], "name": b["name"],
                      "samples": [round(v, 5) for v in x]})
    ein = sum(v * v for v in d["signal"])
    eout = sum(v * v for b in bands for v in b["samples"])
    json.dump({"bands": bands, "energy_in": round(ein, 4),
               "energy_out": round(eout, 4)},
              open(sys.argv[2], "w"), indent=1)

main()
