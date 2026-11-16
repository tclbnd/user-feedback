#!/usr/bin/env python3
import json, sys

def main():
    data = json.load(open(sys.argv[1]))
    w, h = data["w"], data["h"]
    out = [[[c for c in px] for px in row] for row in data["base"]]
    for layer in data["layers"]:
        for i, px in enumerate(layer["pixels"]):
            dx, dy = layer["x"] + i % layer["w"], layer["y"] + i // layer["w"]
            if not (0 <= dx < w and 0 <= dy < h):
                continue
            r, g, b, a = px
            # defect: composites in display space, ignores premultiplied flag
            for c, v in enumerate((r, g, b)):
                out[dy][dx][c] = round(v * a / 255 + out[dy][dx][c] * (1 - a / 255))
    flat = [px for row in out for px in row]
    json.dump({"pixels": flat, "alpha": [255] * len(flat)}, open(sys.argv[2], "w"))

main()
