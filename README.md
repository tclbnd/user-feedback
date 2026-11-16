# Rotation/crop visible-region mapper

Run: `python3 geo.py input.json output.json`

Input `{"frames":[{"id","coded":[w,h],"aperture":[x,y,w,h],
"sar":[sw,sh],"transform":"r90"|"r180"|"r270"|"mirror"|null}...],
"canvas":[W,H]}` — `aperture` is the clean aperture in CODED pixels,
`sar` the sample aspect ratio (pixel w:h), `transform` applied to
the aperture-cropped image before canvas mapping, `mirror` =
horizontal flip. Rotations are counter-clockwise by default? NO —
use CLOCKWISE degrees per this contract: r90 rotates the image 90°
clockwise, so output WxH swaps for r90/r270.

Per frame, the DISPLAY region is the aperture rect rescaled by sar
(`disp_w = apert_w * sw/sh`, `disp_h = apert_h`), then transformed
(swap w/h for r90/r270). Map that display rect into the canvas with
strategy `"fit"`: scale = min(W/dw, H/dh), centered, corners rounded
to half-pixel (round(v*2)/2, 3 decimals). Output
`{"frames":[{"id","disp":[dw,dh],"out":[w,h],"offset":[x,y],
"corners":[[x0,y0],[x1,y1]]}...]}` where corners are the display
region's top-left/bottom-right inside the canvas, and `"safe"` —
the aperture mapped back: `{"safe":[x,y,w,h]}` a rect inside out.
