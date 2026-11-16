#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    out_tracks = []
    track_map = {}
    for t in d["tracks"]:
        samples = []
        for fr in t["fragments"]:  # defect: arrival order, no sort/seq handling
            cur = fr["bmdt"]
            for s in fr["samples"]:
                samples.append({"pts": cur, "dur": s["duration"], "size": s["size"]})
                cur += s["duration"]
        samples.sort(key=lambda s: s["pts"])
        out_tracks.append({"id": t["id"], "timeline": samples, "gaps": [],
                           "duration_ms": (samples[-1]["pts"] + samples[-1]["dur"]) / t["timescale"]})
        track_map[t["id"]] = (t, samples)
    answers = []
    for q in d["targets"]:
        t, samples = track_map[q["track"]]
        tt = int(q["t_ms"] * t["timescale"] / 1000)
        hit = next((s for s in samples if s["pts"] <= tt < s["pts"] + s["dur"]), None)
        if hit:
            answers.append({"track": q["track"], "t_ms": q["t_ms"], "found": True,
                            "pts": hit["pts"], "dur": hit["dur"], "size": hit["size"]})
        else:
            answers.append({"track": q["track"], "t_ms": q["t_ms"], "found": False,
                            "pts": None, "dur": None, "size": None})
    json.dump({"tracks": out_tracks, "answers": answers}, open(sys.argv[2], "w"))

main()
