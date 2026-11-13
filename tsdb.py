#!/usr/bin/env python3
"""Layered out-of-order TS buffer (flat buggy baseline)."""
import json, sys, hashlib

def run(req):
    wm, caps = req["watermark"], req["levels"]
    buf, late, comp = [], 0, 0
    max_ts = 0
    for p in req["points"]:
        max_ts = max(max_ts, p["ts"])
        buf.append(dict(p))                    # BUG: no watermark check, no layering
        if len(buf) > caps[0]:
            comp += 1                          # fake compaction, keeps everything
    results = []
    for q in req["queries"]:
        at, key = q["at"], q.get("key")
        pts = [p for p in buf if p["ts"] <= at and (key is None or p["key"] == key)]
        pts.sort(key=lambda p: p["ts"])        # BUG: write-order ties, no ver dedup
        results.append({"at": at, "key": key, "points": pts})
    return {"level_stats": {"L0": len(buf), "L1": 0, "L2": 0, "late": late,
                            "compactions": comp},
            "results": results,
            "digest": hashlib.sha256(repr(buf).encode()).hexdigest()[:16]}

def main():
    req = json.load(open(sys.argv[1]))
    json.dump(run(req), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
