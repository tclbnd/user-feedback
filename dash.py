#!/usr/bin/env python3
import json, sys

def expand(tl, timescale, pto, pstart, pend):
    segs = []; cur = 0
    for i, e in enumerate(tl):
        t = e.get("t", cur); d = e["d"]; r = e.get("r", 0)
        if r < 0:
            # BUG 1: negative repeat expands once, never clamps to boundary
            count = 1
        else:
            count = r + 1
        for _ in range(count):
            m0, m1 = t, t + d
            segs.append({"n": len(segs),
                         "start": round(pstart + (m0 - pto) / timescale, 6) if False
                                  else round(pstart + m0 / timescale, 6),   # BUG 2: pto ignored
                         "end": round(pstart + m1 / timescale, 6)})
            t = m1; cur = t
    return segs

def main():
    data = json.load(open(sys.argv[1]))
    tracks = {}; periods = []; pstart = 0.0
    for p in data["periods"]:
        pend = pstart + p["duration"] if p.get("duration") is not None else None
        pend_out = pstart
        for tr in p["tracks"]:
            segs = expand(tr["timeline"], tr["timescale"], tr["pto"], pstart, pend)
            tracks[tr["id"]] = {"segments": segs}
            if segs: pend_out = max(pend_out, segs[-1]["end"])
        if pend is not None: pend_out = pend
        periods.append({"id": p["id"], "start": round(pstart, 6), "end": round(pend_out, 6)})
        pstart = pend_out
    seeks = []
    for s in data.get("seeks", []):
        hit = {"n": None, "gap": True}
        for seg in tracks[s["track"]]["segments"]:
            if seg["start"] <= s["t"] <= seg["end"]:   # BUG 3: closed end -> boundary double-hit
                hit = {"n": seg["n"], "gap": False}; break
        seeks.append({"t": s["t"], "track": s["track"], "n": hit["n"], "gap": hit["gap"]})
    json.dump({"tracks": tracks, "periods": periods, "seeks": seeks},
              open(sys.argv[2], "w"), indent=1)

main()
