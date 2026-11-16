# Frame compositor (alpha + linear light)

Run: `python3 comp.py input.json output.json`

Input JSON: `base` (W×H pixel array, each pixel `[r,g,b]` 8-bit sRGB values),
`layers` ordered source-over, each `{"x","y","w","h","pixels"` (w*h array of
`[r,g,b,a]` 8-bit, `premultiplied` bool, `pts` display-order int) and
`base_pts` int. A layer only composites where `pts` ordering places it
inside the base window: layers are clipped to the base rect; overlapping
regions of later (higher) layers replace earlier composite, never
double-accumulate.

Pipeline per destination pixel, applied back-to-front in `layers` order:
convert source to non-premultiplied (if `premultiplied`, divide rgb by a,
a=0 pixels have undefined color → treated as transparent black), convert
sRGB to linear (`pow((v/255), 2.2)`), composite `out = src + dst*(1-a_l)`
with a_l = a/255, convert back to sRGB and round half-up. Output
`{"pixels":[...base flattened...], "alpha":[effective coverage a*255 per pixel]}`.
