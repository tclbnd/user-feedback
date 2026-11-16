#!/usr/bin/env python3
import json, sys, math

ROWS = [697, 770, 852, 941]
COLS = [1209, 1336, 1477, 1633]
KEYS = ["123A", "456B", "789C", "*0#D"]


def goertzel(sig, freq, sr):
    c = 2 * math.cos(freq * 2 * math.pi / sr)
    s0 = s1 = s2 = 0.0
    for x in sig:
        s0 = x + c * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - c * s1 * s2


def detect(sig, sr, win, hop, thr):
    hits = []
    for off in range(0, max(1, len(sig) - win + 1), hop):
        seg = sig[off:off + win]
        rows = [(goertzel(seg, f, sr), i) for i, f in enumerate(ROWS)]
        cols = [(goertzel(seg, f, sr), i) for i, f in enumerate(COLS)]
        re, ri = max(rows); ce, ci = max(cols)
        hits.append((KEYS[ri][ci], off, off + win, re, ce) if re >= thr and ce >= thr else None)
    return hits


def main():
    d = json.load(open(sys.argv[1]))
    left, right = d["samples"][0], d["samples"][1]
    sr = d["sample_rate"]; win = d["tone_ms"] * sr // 1000; hop = win // 2
    thr = d["threshold"]; fade = d["fade"]
    # defect: detect on left only; mute left only; hard cut without ramps
    events = []; cur = None
    for h in detect(left, sr, win, hop, thr):
        if h is None:
            if cur: events.append(cur); cur = None
            continue
        key, s, e, re, ce = h
        if cur and cur["key"] == key:
            cur["end"] = e
        else:
            if cur: events.append(cur)
            cur = {"key": key, "start": s, "end": e,
                   "confidence": round(min(re, ce) / thr, 3)}
    if cur: events.append(cur)
    out_l, out_r = left[:], right[:]
    for ev in events:
        for i in range(max(0, ev["start"]), min(len(left), ev["end"])):
            out_l[i] = 0
    json.dump({"events": events, "left": out_l, "right": out_r, "twist": {}},
              open(sys.argv[2], "w"))

main()
