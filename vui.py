#!/usr/bin/env python3
import json, sys

FIELDS = ("range", "primaries", "transfer", "matrix")

def resolve_seg(seg, state):
    vui = seg.get("vui") or {}; con = seg.get("container") or {}
    override = con.get("override", False)
    out = []
    for n in range(seg["frames"]):
        rec = {"seg": seg["id"], "n": n}; src = {}
        for f in FIELDS:
            if f in vui:            # BUG: vui always wins; override ignored,
                v, s = vui[f], "vui"        #       and missing fields reset
            elif f in con:                #       instead of inheriting
                v, s = con[f], "container"
            else:
                v, s = "unspecified", "default"
            rec[f] = v; src[f] = s
        rec["source"] = src
        out.append(rec)
    return out

def main():
    d = json.load(open(sys.argv[1]))
    state = {}; frames = []
    for seg in d["segments"]:
        rows = resolve_seg(seg, state)
        frames.extend(rows)
    queries = []
    base = 0
    for seg in d["segments"]:
        for q in d.get("queries", []):
            if base <= q["at"] < base + seg["frames"]:
                local = resolve_seg(seg, {})
                r = dict(local[q["at"] - base]); r["at"] = q["at"]
                queries.append(r)
        base += seg["frames"]
    json.dump({"frames": frames, "queries": queries}, open(sys.argv[2], "w"), indent=1)

main()
