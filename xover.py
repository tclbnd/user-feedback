#!/usr/bin/env python3
"""Multi-crossover speaker band router CLI.

Legacy input shape (mono signal, no events) keeps the original output
schema exactly; the delay is applied as a true RIGHT shift
(y[n] = x[n-d], zero-filled head) -- the previous left shift made a
driver branch arrive early and cancelled the main channel inside the
crossover band.

Extended inputs (events / explicit coefficients / multi-channel) run
through the stateful engine in xover_engine and additionally emit the
per-branch synthesis response ("mix"), edge table and a full monitor
"state" snapshot needed to rebuild processing under another block size.
"""
import json
import sys
import math

from xover_engine import run_pipeline

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

def is_stereo(signal):
    return bool(signal) and isinstance(signal[0], list)


def run_legacy(d):
    """Original per-band IIR routing with the delay-direction fix."""
    sr = d["sr"]
    bands = []
    for b in d["bands"]:
        lo, hi = b["range"]
        x = d["signal"]
        if lo > 0:
            x = hp(x, lo, sr)
        if hi < sr / 2:
            x = lp(x, hi, sr)
        # RIGHT shift by exactly d samples: y[n] = x[n-d], so the head
        # holds d zeros and y[d] = x[0] (matches the engine DelayLine).
        sh = round(d["delay_ms"].get(b["driver"], 0) * sr / 1000)
        if sh:
            x = [0.0] * sh + x[:len(x) - sh]
        bands.append({"driver": b["driver"], "name": b["name"],
                      "samples": [round(v, 5) for v in x]})
    ein = sum(v * v for v in d["signal"])
    eout = sum(v * v for b in bands for v in b["samples"])
    return {"bands": bands, "energy_in": round(ein, 4),
            "energy_out": round(eout, 4)}


def main():
    d = json.load(open(sys.argv[1]))
    extended = (
        is_stereo(d["signal"])
        or bool(d.get("events"))
        or bool(d.get("polarity"))
        or any(isinstance(xo, dict) for xo in d.get("xovers", [])))
    result = run_pipeline(d) if extended else run_legacy(d)
    with open(sys.argv[2], "w") as fh:
        json.dump(result, fh, indent=1)


if __name__ == "__main__":
    main()
