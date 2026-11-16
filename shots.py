#!/usr/bin/env python3
import json, sys

def dist(a, b): return sum(abs(x - y) for x, y in zip(a, b)) / 2

def main():
    d = json.load(open(sys.argv[1]))
    th = d["thresholds"]; fr = d["frames"]
    bounds = []; flashes = []; last_t = -1e18
    i = 0
    while i + 1 < len(fr):
        dd = dist(fr[i]["hist"], fr[i + 1]["hist"])
        if dd >= th["hard"]:
            # BUG: no flash suppression — every spike is a cut
            # BUG: no merge window — consecutive hard cuts all kept
            bounds.append({"t": fr[i + 1]["t"], "type": "hard",
                           "confidence": round(min(dd / th["hard"], 1.5), 3)})
            i += 1
        elif dd >= th["grad"]:
            j = i; acc = 0.0
            while j + 1 < len(fr):
                dd2 = dist(fr[j]["hist"], fr[j + 1]["hist"])
                if dd2 < th["grad"]:
                    break
                acc += dd2; j += 1
            if j - i + 1 >= 3 and acc >= th["hard"]:
                bounds.append({"t": fr[j]["t"], "type": "gradual",
                               "confidence": round(min(acc / th["hard"], 1.5), 3),
                               "span": [fr[i]["t"], fr[j]["t"]]})
            i = j
        else:
            i += 1
    json.dump({"boundaries": bounds, "flashes": flashes}, open(sys.argv[2], "w"), indent=1)

main()
