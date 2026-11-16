#!/usr/bin/env python3
import json, sys

M = 1 << 33

def main():
    d = json.load(open(sys.argv[1]))
    out = []; programs = {}; discs = []
    epoch = 0; last_pcr = None; last_prog = None  # defect: single global epoch, no per-program, 32-bit half
    for i, p in enumerate(d["packets"]):
        if p["program"] != last_prog and last_prog is not None:
            discs.append({"index": i, "kind": "program_switch", "detail": f"{last_prog}->{p['program']}"})
        last_prog = p["program"]
        rec = {"pts64": None, "pcr64": None, "ms": None}
        if p["pcr"] is not None:
            if last_pcr is not None and p["pcr"] < last_pcr - (1 << 32):
                epoch += M
                discs.append({"index": i, "kind": "wrap", "detail": "pcr wrap"})
            last_pcr = p["pcr"]
            rec["pcr64"] = p["pcr"] + epoch
            rec["ms"] = round(rec["pcr64"] / 90, 3)
        if p["pts"] is not None:
            rec["pts64"] = p["pts"] + epoch
            if rec["pcr64"] is None:
                rec["ms"] = round(rec["pts64"] / 90, 3)
        out.append(rec)
    programs[last_prog] = {"epoch": epoch, "last_pcr": last_pcr}
    json.dump({"packets": out, "programs": programs, "discontinuities": discs},
              open(sys.argv[2], "w"))

main()
