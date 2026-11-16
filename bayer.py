#!/usr/bin/env python3
import json, sys

SITES = {
    "RGGB": {(0,0):"R",(0,1):"G",(1,0):"G",(1,1):"B"},
    "BGGR": {(0,0):"B",(0,1):"G",(1,0):"G",(1,1):"R"},
    "GRBG": {(0,0):"G",(0,1):"R",(1,0):"B",(1,1):"G"},
    "GBRG": {(0,0):"G",(0,1):"B",(1,0):"R",(1,1):"G"},
}


def site(y, x):  # defect: pattern ignored, hardcoded RGGB
    return SITES["RGGB"][(y % 2, x % 2)]


def main():
    d = json.load(open(sys.argv[1]))
    w, h = d["width"], d["height"]
    raw = [row[:] for row in d["raw"]]
    corrected = []; uncorrectable = []
    for y, x in d["bad_pixels"]:
        vals = []
        for dy in (-1, 0, 1):  # defect: all 8 neighbors, mixes color sites
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0: continue
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w:
                    vals.append(raw[ny][nx])
        if not vals:
            uncorrectable.append([y, x]); continue
        vals.sort(); new = vals[len(vals) // 2]
        corrected.append([y, x, raw[y][x], new]); raw[y][x] = new
    out = []
    for y in range(h):
        for x in range(w):
            c = site(y, x)
            ch = {"R": raw[y][x] if c == "R" else 0,
                  "G": raw[y][x] if c == "G" else 0,
                  "B": raw[y][x] if c == "B" else 0}
            for want in "RGB":
                if c == want: continue
                vals = []
                for dy, dx in ((0,-1),(0,1),(-1,0),(1,0)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and site(ny, nx) == want:
                        vals.append(raw[ny][nx])
                ch[want] = round(sum(vals) / len(vals)) if vals else 0
            out.append([ch["R"], ch["G"], ch["B"]])
    json.dump({"rgb": out, "corrected": corrected, "uncorrectable": uncorrectable},
              open(sys.argv[2], "w"))

main()
