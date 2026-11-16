#!/usr/bin/env python3
import json, sys

FIELDS = ("range", "primaries", "transfer", "matrix")

def resolve_field(field, vui, con, override, state):
    """Resolve one colour attribute, independently of the other fields."""
    if override and field in con:   # container claims precedence, but only
        return con[field], "container"  # for fields it actually declares
    if field in vui:
        return vui[field], "vui"
    if field in con:
        return con[field], "container"
    if field in state:              # keep what the previous frame resolved
        return state[field], "inherited"
    return "unspecified", "default"


def resolve_seg(seg, state):
    vui = seg.get("vui") or {}; con = seg.get("container") or {}
    override = bool(con.get("override", False))
    out = []
    for n in range(seg["frames"]):
        rec = {"seg": seg["id"], "n": n}; src = {}
        for f in FIELDS:
            v, s = resolve_field(f, vui, con, override, state)
            rec[f] = v; src[f] = s
            # Commit immediately so the next frame inherits this frame's
            # state and a later segment never rewrites earlier frames.
            state[f] = v
        rec["source"] = src
        out.append(rec)
    return out

def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: vui.py input.json output.json")
    with open(sys.argv[1]) as fh:
        d = json.load(fh)
    state = {}; frames = []
    for seg in d["segments"]:
        frames.extend(resolve_seg(seg, state))
    queries = []
    base = 0
    for seg in d["segments"]:
        wanted = [q["at"] - base for q in d.get("queries", [])
                  if base <= q["at"] < base + seg["frames"]]
        if wanted:
            # A seek starts from the containing segment alone: the segment's
            # own config is the only state, nothing carries over from before.
            local = resolve_seg(seg, {})
            for n in wanted:
                r = dict(local[n]); r["at"] = base + n
                queries.append(r)
        base += seg["frames"]
    with open(sys.argv[2], "w") as fh:
        json.dump({"frames": frames, "queries": queries}, fh, indent=1)

if __name__ == "__main__":
    main()
