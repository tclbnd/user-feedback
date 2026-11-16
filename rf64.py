#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    nch = d["channels"]; data = d["interleaved"]
    frames = len(data) // nch  # defect: ignores ds64/riff_size entirely
    out = []
    for q in d["requests"]:
        s, c = q["start_frame"], q["count"]
        seg = data[s * nch:(s + c) * nch]  # defect: no bounds check, negative indexes wrap
        if q.get("channel") == "all":
            out.append({"id": q["id"], "channel": "all", "start_frame": s, "samples": seg})
        else:
            ch = q["channel"]
            out.append({"id": q["id"], "channel": ch, "start_frame": s,
                        "samples": seg[ch::nch]})
    json.dump({"results": out, "frames_total": frames}, open(sys.argv[2], "w"))

main()
