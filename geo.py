#!/usr/bin/env python3
import json, sys

def disp_size(a, sar, tr):
    dw = a[2] * sar[0] / sar[1]; dh = a[3]
    if tr in ("r90", "r270"):
        dw, dh = dh, dw          # BUG 1: swap before sar — wrong order
    elif tr == "mirror":
        pass                     # BUG 2: mirror leaves aspect unchanged — fine,
                                 #        but x offset flip ignored below
    return dw, dh

def main():
    d = json.load(open(sys.argv[1]))
    W, H = d["canvas"]; out = []
    for f in d["frames"]:
        a = f["aperture"]; tr = f.get("transform")
        dw, dh = disp_size(a, f["sar"], tr)
        s = min(W / dw, H / dh)
        ow, oh = dw * s, dh * s
        ox, oy = (W - ow) / 2, (H - oh) / 2
        r = lambda v: round(round(v * 2) / 2, 3)
        out.append({"id": f["id"], "disp": [r(dw), r(dh)],
                    "out": [r(ow), r(oh)], "offset": [r(ox), r(oy)],
                    "corners": [[r(ox), r(oy)], [r(ox + ow), r(oy + oh)]],
                    "safe": [r(ox), r(oy), r(ow), r(oh)]})
    json.dump({"frames": out}, open(sys.argv[2], "w"), indent=1)

main()
