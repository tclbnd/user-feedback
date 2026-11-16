#!/usr/bin/env python3
import json, sys

def nominal(n, d): return round(n / d)

def to_index(label, n, d, drop):
    hh, mm, ss, ff = [int(x) for x in label.replace(";", ":").split(":")]
    fps = nominal(n, d)
    total_min = hh * 60 + mm
    index = (total_min * 60 + ss) * fps + ff
    if drop:
        # defect: drops 2 every minute including the 10th
        index -= 2 * total_min
    return index

def to_label(index, n, d, drop):
    fps = nominal(n, d)
    secs, ff = divmod(index, fps)
    hh, rem = divmod(secs, 3600)
    mm, ss = divmod(rem, 60)
    if drop:
        # defect: adds back dropped labels on every minute
        ff += 2 * (hh * 60 + mm)
    sep = ";" if drop else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"

def main():
    data = json.load(open(sys.argv[1]))
    out = []
    for t in data["tasks"]:
        n, d, drop = t["fps_num"], t["fps_den"], t["drop_frame"]
        ops = []
        for op in t["ops"]:
            try:
                if op["dir"] == "label_to_index":
                    r = {"valid": True, "index": to_index(op["label"], n, d, drop), "label": op["label"]}
                elif op["dir"] == "index_to_label":
                    r = {"valid": True, "index": op["index"], "label": to_label(op["index"], n, d, drop)}
                else:
                    idx = to_index(op["label"], n, d, drop)
                    r = {"valid": True, "index": idx,
                         "label": to_label(idx, op["to_fps_num"], op["to_fps_den"], op["to_drop"])}
            except Exception:
                r = {"valid": False, "label": None, "index": None}
            ops.append(r)
        out.append({"id": t["id"], "ops": ops})
    json.dump({"tasks": out}, open(sys.argv[2], "w"))

main()
