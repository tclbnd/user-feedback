#!/usr/bin/env python3
"""YUV<->RGB conversion across standards and ranges."""
import json, sys

K = {"bt601": (0.299, 0.114), "bt709": (0.299, 0.114), "bt2020": (0.2627, 0.0593)}

def lim(v, lo, hi):
    return lo + v * (hi - lo)

def unlim(v, lo, hi):
    return (v - lo) / (hi - lo)

def rgb_to_yuv(r, g, b, std, rgb_rng, yuv_rng):
    kr, kb = K[std]
    if rgb_rng == "limited":
        r, g, b = unlim(r, 16, 235), unlim(g, 16, 235), unlim(b, 16, 235)
    # BUG: limited scaling applied before matrix on the YUV side, wrong gain
    y = kr * r + (1 - kr - kb) * g + kb * b
    cb = 0.5 / (1 + kb) * (b - y) + 0.5
    cr = 0.5 / (1 + kr) * (r - y) + 0.5
    if yuv_rng == "limited":
        y = lim(y, 16, 235); cb = lim(cb - 0.5, 16, 240) + 128 - 112 + 16  # muddled
        cr = lim(cr - 0.5, 16, 240) + 128 - 112 + 16
    return [round(y * 255) if yuv_rng == "full" else round(y),
            round(cb * 255) if yuv_rng == "full" else round(cb),
            round(cr * 255) if yuv_rng == "full" else round(cr)]

def yuv_to_rgb(y, cb, cr, std, yuv_rng, rgb_rng):
    kr, kb = K[std]
    if yuv_rng == "limited":
        y = unlim(y, 16, 235); cb = unlim(cb, 16, 240); cr = unlim(cr, 16, 240)
    else:
        y /= 255; cb = cb / 255 - 0.5 + 0.5 * 255 / 255  # muddled normalization
        cr = cr / 255 - 0.5 + 0.5 * 255 / 255
    b = y + (1 + kb) * (cb - 0.5)
    r = y + (1 + kr) * (cr - 0.5)
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    pix = [r, g, b]
    if rgb_rng == "limited":
        pix = [lim(v, 16, 235) for v in pix]
    else:
        pix = [v * 255 for v in pix]
    return [min(255, max(0, round(v))) for v in pix]

def main():
    req = json.load(open(sys.argv[1]))
    std = req["standard"]
    out = {"ops": [], "warnings": []}
    meta_std = (req.get("meta") or {}).get("standard")
    if meta_std and meta_std != std:
        out["warnings"].append("mixed-standard")
    for op in req["ops"]:
        pixels = []
        for p in op["pixels"]:
            if op["direction"] == "rgb-to-yuv":
                pixels.append(rgb_to_yuv(*p, std, req["rgb_range"], req["yuv_range"]))
            else:
                pixels.append(yuv_to_rgb(*p, std, req["yuv_range"], req["rgb_range"]))
        entry = {"pixels": pixels}
        if req.get("roundtrip"):
            back = []
            for q in pixels:
                if op["direction"] == "rgb-to-yuv":
                    back.append(yuv_to_rgb(*q, std, req["yuv_range"], req["rgb_range"]))
                else:
                    back.append(rgb_to_yuv(*q, std, req["rgb_range"], req["yuv_range"]))
            src = op["pixels"]
            entry["roundtrip"] = {"max_err": max(abs(a - b) for s, t in zip(src, back) for a, b in zip(s, t))}
        out["ops"].append(entry)
    json.dump(out, open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
