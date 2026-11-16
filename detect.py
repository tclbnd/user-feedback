#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    w = d["windows"]; fm = d["frame_ms"]
    events = []
    run = None
    prev_pts = None
    for f in d["frames"]:
        kind = []
        if f["luma"] < w["black_luma"]: kind.append("black")
        if f["diff"] < w["freeze_diff"]: kind.append("freeze")
        if not kind:
            if run: events.append(run); run = None
            prev_pts = f["pts"]; continue
        k = kind[0]
        # defect: no merge across gaps, no both-handling, min uses frame count not ms
        if run and run["kind"] == k:
            run["frames"] += 1; run["end"] = f["pts"] + fm
        else:
            if run: events.append(run)
            run = {"kind": k, "start": f["pts"], "end": f["pts"] + fm, "frames": 1, "reason": k}
        prev_pts = f["pts"]
    if run: events.append(run)
    kept = [e for e in events if e["frames"] >= (2 if e["kind"] == "black" else 3)]
    playable = []
    cursor = d["frames"][0]["pts"] if d["frames"] else 0
    for e in sorted(kept, key=lambda x: x["start"]):
        if e["start"] > cursor:
            playable.append({"kind": "playable", "start": cursor, "end": e["start"],
                             "frames": 0, "reason": "playable"})
        cursor = max(cursor, e["end"])
    end = d["frames"][-1]["pts"] + fm if d["frames"] else cursor
    if cursor < end:
        playable.append({"kind": "playable", "start": cursor, "end": end, "frames": 0, "reason": "playable"})
    json.dump({"events": kept, "playable": playable, "skipped": []}, open(sys.argv[2], "w"))

main()
