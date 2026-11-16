#!/usr/bin/env python3
import json, sys

CTRL = {
    0x25: "roll",       # BUG 1: RU2/RU3 row count ignored entirely
    0x2C: "edm", 0x2E: "enm", 0x2F: "eoc",
    0x20: "rcl", 0x2D: "cr", 0x21: "bs", 0x29: "rdc",
}

def main():
    d = json.load(open(sys.argv[1]))
    disp = ["", "", "", ""]; nondisp = ["", "", "", ""]
    mode = "pop-on"; row = 3; snaps = []; modes = []
    prev_ctrl = None
    for ev in d["events"]:
        rows = 3 if mode == "roll-up" else 4
        for pair in ev["data"]:
            lo, hi = pair[0], pair[1] if len(pair) > 1 else 0
            if lo in (0x14, 0x1C) and hi in CTRL:
                op = CTRL[hi]
                if op == prev_ctrl:
                    continue              # dedup adjacent repeats
                prev_ctrl = op
                if op == "roll":
                    mode = "roll-up"; modes.append({"t": ev["t"], "mode": "roll-up"})
                elif op == "rcl":
                    mode = "pop-on"; modes.append({"t": ev["t"], "mode": "pop-on"})
                elif op == "rdc":
                    mode = "paint-on"; modes.append({"t": ev["t"], "mode": "paint-on"})
                elif op == "edm":
                    disp = ["", "", "", ""]
                elif op == "enm":
                    nondisp = ["", "", "", ""]
                elif op == "eoc":
                    disp, nondisp = nondisp, disp   # BUG 2: swap keeps stale
                elif op == "cr":                    #       rows unaligned —
                    disp = disp[1:] + [""]          #       no row reset
                elif op == "bs":
                    tgt = disp if mode != "pop-on" else nondisp
                    tgt[row] = tgt[row][:-1]
                continue
            prev_ctrl = None
            tgt = nondisp if mode == "pop-on" else disp
            ch = chr(lo) if 0x20 <= lo < 0x7F else ""
            ch += chr(hi) if 0x20 <= hi < 0x7F else ""
            tgt[row] += ch
        snaps.append({"t": ev["t"], "screen": [r.rstrip() for r in disp]})
    json.dump({"snapshots": snaps, "modes": modes}, open(sys.argv[2], "w"), indent=1)

main()
