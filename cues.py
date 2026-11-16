#!/usr/bin/env python3
import json, sys

def resolve(prog):
    cues = list(prog["cues"])
    by_id = {}
    for c in cues:
        # BUG 1: version comparison ignores component scope only —
        #        and, worse, dedup keyed globally per id+cue version
        key = c["id"]
        if key in by_id and by_id[key]["version"] >= c["version"]:
            c["_superseded"] = True
        else:
            if key in by_id:
                by_id[key]["_superseded"] = True
            by_id[key] = c
    cancel_targets = {c["target"] for c in cues if c["type"] == "cancel" and not c.get("_superseded")}
    # BUG 2: returns raw cancel-target set; caller applies across all programs
    return cues, cancel_targets

def cuts_for(prog, global_cancel):
    cues, _ = resolve(prog)
    out = []
    for c in cues:
        if c["type"] == "cancel":
            continue
        state = "superseded" if c.get("_superseded") else "active"
        if c["id"] in global_cancel and state == "active":
            state = "cancelled"
        comps = [cc["id"] for cc in prog["components"]] if c["type"] == "per-component" else [c["component"]]
        for comp in comps:
            out.append({"cue": c["id"], "component": comp,
                        "at": c.get("at", 0), "state": state, "version": c["version"]})
    out.sort(key=lambda x: (x["at"], x["component"], x["cue"]))
    return out

def main():
    d = json.load(open(sys.argv[1]))
    global_cancel = set()
    for p in d["programs"]:
        _, t = resolve(p)
        global_cancel |= t          # BUG 2: cancel leaks across programs
    programs = {p["id"]: {"cuts": cuts_for(p, global_cancel)} for p in d["programs"]}
    resume = []
    for r in d.get("resume", []):
        ids = sorted(c["cue"] for c in programs[r["program"]]["cuts"]
                     if c["state"] == "active" and c["at"] >= r["from"])
        resume.append({"program": r["program"], "from": r["from"], "cuts": ids})
    json.dump({"programs": programs, "resume": resume}, open(sys.argv[2], "w"), indent=1)

main()
