# Rotation/crop visible-region mapper

Run: `python3 geo.py input.json output.json`

Input `{"frames":[{"id","coded":[w,h],"aperture":[x,y,w,h],
"sar":[sw,sh],"transform":"r90"|"r180"|"r270"|"mirror"|null}...],
"canvas":[W,H]}` — `aperture` is the clean aperture in CODED pixels,
`sar` the sample aspect ratio (pixel w:h), `transform` applied to
the aperture-cropped image before canvas mapping, `mirror` =
horizontal flip. Rotations are CLOCKWISE: r90 rotates the image 90°
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

## Corrections applied

The decoder hands over a *coded* frame plus a clean aperture, and the
geometry must be interpreted in that order:

1. **Visible image.** The clean aperture is read in coded-sample
   coordinates (honouring its `x`/`y` origin) and clipped to the coded
   frame. Nothing outside the decoded samples is ever scaled up.
2. **Square pixels.** The horizontal axis is divided by the sample
   aspect ratio (`sar`), so `disp_w = apert_w * sw/sh`, `disp_h = apert_h`.
   Non-square pixels are therefore never mistaken for square ones.
3. **Orientation.** The *desqueezed* image is then rotated (clockwise,
   per the contract above) or mirrored. Rotation is an affine map, not
   just a width/height swap, and it happens **after** the SAR
   conversion: a 720x576 anamorphic frame turned 90° is 576x1024, not
   576x720.
4. **Canvas.** The transformed image is fitted into the canvas
   (`scale = min(W/dw, H/dh)`) and centred. Other strategies are
   `"fill"` (cover), `"stretch"`, and `"none"` (1:1). Content may leave
   the target under `"fill"`; offsets are then negative.

`disp` is the oriented, square-pixel visible size. `out` is the
post-scale size. `offset` is the top-left of the content inside the
canvas, `corners` are the content boundary, and `safe` is the
subtitle-safe rect transformed by exactly the same matrix (it defaults
to the full visible image when no safe area is supplied).

Coordinates are kept as exact rationals throughout and snapped to the
half-pixel grid (`round(v*2)/2`, half-to-even) only when serialised, so
half-pixel positions and negative offsets survive the round trip.

Optional per-frame fields: `"strategy"` (overrides the top-level
strategy), `"safe"` / `"safe_area"` (a subtitle-safe rect, either in
coded pixels or — when every component lies in [0, 1] — as a fraction
of the visible image; use `{"rect": ..., "relative": true|false}` to
force either reading). `transform` also accepts `"r90+mirror"`,
`{"rotation": 270, "mirror": true}`, or an explicit 2x2 / 3x3 / affine
matrix.

Tests: `python3 -m unittest discover -s tests -v`, plus
`python3 tests/property_check.py` for the randomised differential check
against an independent reference implementation.

