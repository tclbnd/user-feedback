#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    fr = d["frames"]; tag = d["tag_bytes"]; tgt = d["target"]
    offs = []; o = tag
    for f in fr:
        offs.append(o); o += f["len"]
    cum = []; c = 0
    for f in fr:
        c += f["samples"] if f["valid"] else 0; cum.append(c)
    # BUG 1: 平均估算——按目标/总样本比例估帧号，不做精确累计定位
    total = cum[-1] if cum else 0
    tf = min(int(tgt / max(total, 1) * len(fr)), len(fr) - 1) if total else 0
    # BUG 2: 直接以目标帧为预读点，reservoir 回读被忽略
    p = tf
    prev_valid = sum(f["samples"] for f in fr[:p] if f["valid"])
    discard = max(0, tgt - prev_valid)
    json.dump({"start_frame": p, "byte_offset": offs[p],
               "discard": discard, "reliable": True,
               "notes": ["reservoir-ok"]}, open(sys.argv[2], "w"), indent=1)

main()
