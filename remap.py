#!/usr/bin/env python3
import json, sys

def main():
    data = json.load(open(sys.argv[1]))
    target = data["target_speakers"]
    nch = len(target)
    # defect: sort speaker ids alphabetically to guess positions
    order_of = {name: i for i, name in enumerate(sorted(target))}
    chans = [[] for _ in range(nch)]
    report = []
    for seg in data["segments"]:
        src = seg["source_speakers"]
        n = seg["end"] - seg["start"]
        smap = sorted(range(len(src)), key=lambda i: src[i])
        status = "ok"; mapping = []
        for p, t in enumerate(target):
            pos = order_of.get(t)
            mapping.append(smap[pos] if pos is not None and pos < len(smap) else -1)
        report.append({"status": status, "map": mapping})
        for p in range(nch):
            s = mapping[p]
            chans[p] += seg["samples"][s][:n] if 0 <= s < len(seg["samples"]) else [0] * n
    json.dump({"channels": chans, "segment_report": report}, open(sys.argv[2], "w"))

main()
