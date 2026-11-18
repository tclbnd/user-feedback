# Adaptive deblocking filter

Run: `python3 deblock.py input.json output.json`

## Input

`{"w","h","pixels":[rows of ints],"block":B, ...}` — luma grid with block
transform edges every B pixels. Fixture fields:

| field | meaning |
|---|---|
| `qp` | quantisation parameter; drives the filter strength |
| `bitdepth` | sample bit depth, default 8; outputs saturate to `[0, 2**bitdepth-1]` |
| `strength` | optional artefact-activity floor (legacy fixtures without `qp`) |
| `protect` | optional inward-gradient threshold for real texture |
| `mask` / `invalid` | optional grid of 0/1; 1 marks an unprocessable sample |

## Boundary classification and strength

For each vertical block boundary at x=kB and horizontal at y=kB the boundary
activity is `A = |mean of the two flanking columns/rows| - ...` (absolute
mean difference of the two columns/rows flanking the edge, computed from
the original frame). The inward texture `T` is the mean gradient one step
further inside each side.

From the QP a quantiser step is derived
(`qstep = 2**((qp-4)/6) * 2**(bitdepth-8)`) and scaled by the block size.
Each boundary is classified:

* `outer` — at the image border, no room for the 2-pixel stencil; skipped;
* `blocked` — flanking samples are entirely inside the unprocessable mask;
* `hard` — `A` exceeds the QP cut-off: a real bright/dark contour, kept;
* `artifact` — `A >= t_lo + T/2` but below the cut-off: filtered;
* `flat` — below the local floor: left untouched.

The same QP cut-off is also applied per sample, and each pixel is kept
whenever its inward gradient exceeds `protect` (real texture).

## Filtering and schedule

Vertical boundaries are processed left→right, then horizontal boundaries
top→bottom. All reads use the **original** input and all writes go to a
separate output buffer, so the input object is never mutated; when the
vertical and horizontal stencils overlap, the first writer (vertical) wins.

Blends are symmetric and unbiased (the original snapshot used one 3:1
formula for every pixel and a biased near-pixel formula):

* the two pixels immediately flanking the edge: `out = (2*self + 2*across + 2) // 4`;
* the pixels one step further out: `out = (3*self + across + 2) // 4`;
* results are saturated to the bit-depth range.

## Output

`{"pixels":[...], "filtered":[[x,y]...]}` — coordinates whose value changed
(sorted row-major) — and `{"edges":[{"at","dir","A","class","strength",
"filtered"}]}` per boundary: activity A rounded to 1 decimal, the chosen
local threshold, the class above and the per-boundary boolean filter flag.

## Acceptance

`python3 make_golden.py` builds the synthetic fixtures and fixed references
in `golden/` (checkerboard, step, diagonal stripe, real hard edge, plus
mask, 12-bit saturation, v/h crossing, outer-edge cases).
`python3 -m unittest -v test_acceptance` performs pixel-exact comparison
against the references and checks that blocking artefacts are reduced while
hard boundaries and texture are retained.
