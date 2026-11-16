#!/usr/bin/env python3
import json, sys

def main():
    d = json.load(open(sys.argv[1]))
    aus = d["access_units"]
    out = []
    for k, e in enumerate(d["entries"]):
        i = e["index"]
        if i >= len(aus) or aus[i]["type"] not in ("IDR", "CRA"):
            out.append({"entry": i, "error": "not-irap"}); continue
        end = d["entries"][k + 1]["index"] if k + 1 < len(d["entries"]) else len(aus)
        emitted = []; skipped = []
        # defect: emits everything including RASL and out-of-range RADL
        for au in aus[i:end]:
            emitted.append(au["poc"])
        out.append({"entry": i, "poc": aus[i]["poc"], "output": emitted, "skipped": skipped})
    json.dump({"entries": out}, open(sys.argv[2], "w"))

main()
