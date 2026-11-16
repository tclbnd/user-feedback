#!/usr/bin/env python3
import json, sys

COEF = {0: [], 1: [1], 2: [2, -1], 3: [3, -3, 1], 4: [4, -6, 4, -1]}

def decode(sf):
    if sf["type"] == "constant":
        return [sf["value"]] * sf["block"]
    if sf["type"] == "verbatim":
        return list(sf["samples"])
    order = sf["order"]
    res = [r["escape"] if isinstance(r, dict) else r
           for r in sf["residuals"]]
    block = sf.get("block", order + len(res))
    if len(sf["warmup"]) != order or len(res) != block - order:
        return None
    s = list(sf["warmup"])
    for r_ in res:
        s.append(r_ + sum(c * s[-k - 1] for k, c in enumerate(COEF[order])))
    return s

def main():
    d = json.load(open(sys.argv[1]))
    out = []
    for fr in d["frames"]:
        chs = [decode(sf) for sf in fr["subframes"]]
        if any(c is None for c in chs):
            out.append({"index": fr["index"], "error": "truncated"})
            continue
        mode = fr["channel_mode"]
        if len(chs) == 2:
            a, b = chs
            if mode == "left_side":
                chs = [a, [x - y for x, y in zip(a, b)]]
            elif mode == "right_side":
                chs = [[x + y for x, y in zip(a, b)], b]
            elif mode == "mid_side":
                # BUG 1: 移位方向写反（L/R 重建互换权重）
                chs = [[m - ((s + 1) >> 1) for m, s in zip(a, b)],
                       [m + (s >> 1) for m, s in zip(a, b)]]
        out.append({"index": fr["index"], "channels": chs})
    json.dump({"frames": out}, open(sys.argv[2], "w"), indent=1)

main()
