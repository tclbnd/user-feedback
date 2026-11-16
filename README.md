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
using the source alpha *before* any mask is applied, a=0 pixels have
undefined color → treated as transparent black), convert sRGB to linear
(`pow((v/255), 2.2)`), composite `out = src + dst*(1-a_l)` with
a_l = a/255, convert back to sRGB and round half-up. Output
`{"pixels":[...base flattened...], "alpha":[effective coverage a*255 per pixel]}`.

## Fixed behaviour

The original implementation mixed display-encoded samples and ignored the
`premultiplied` flag, so semi-transparent coloured overlays grew dark fringes
on light backgrounds. The compositor now:

* **Decodes the alpha attributes of each layer** — `premultiplied` sources are
  un-premultiplied *before* the transfer function (clamped to `[0,1]`), so
  straight and premultiplied material produce the same linear colour.
* **Composites inside linear light**, not in display code values. Only the
  final result is encoded back to sRGB.
* **Normalises output** — `round-half-up` quantisation (not banker's
  rounding), and zero coverage always exports transparent black `[0,0,0]`,
  never leaked sample values.
* **Keeps transparent pixels undefined** — `a == 0` contributes nothing,
  whatever RGB happens to be stored there.
* **Aligns overlays by PTS** — `pts` is the overlay start; an optional
  `[start, end]`, `end_pts` or `duration` bounds its valid display interval.
  Layers are dropped outside that interval and clipped to the base rect.
* **Supports high bit depth** — `max_value`, `maxval` or `bit_depth` on the
  document (or per layer) sets the integer sample range; full float precision
  is kept until the final 8-bit quantisation.
* **Handles layer clipping/overlap exactly once** — `clip` (or
  `clip_x/y/w/h`) restricts where an overlay may draw; `crop` (or
  `sx/sy/cw/ch`) selects a sub-rectangle of a padded source while keeping it
  in place. Every destination pixel is evaluated at most once per layer, so
  overlapping tiles never composite twice. The same guarantee holds when a
  single layer is delivered as several `regions`/`tiles` blocks: overlapping
  pixels are taken from the first region that covers them and are not blended
  again. The chunk size (`block`/`tile`/`chunk` in the document, or
  `COMP_BLOCK`) changes only traversal order, never the arithmetic.

Optional `mask` (one coverage sample per source pixel) scales a layer's alpha
while leaving its colour untouched. The exported `alpha` channel is the
effective coverage after all layers, for verifying the intermediate
transparency.
