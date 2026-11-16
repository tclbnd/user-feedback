# Adaptive deblocking filter

Run: `python3 deblock.py input.json output.json`
(or `deblock.deblock(doc)` from Python; the input document is never modified)

Input `{"w","h","pixels":[rows of ints],"block":B,"strength":S,
"protect":P}` — luma grid, block edges every B pixels.

Optional input keys:

- `qp` — quantiser. Used to derive `strength` when it is not given, and to
  bound how large an across-seam step can still be blocking residue.
- `bits` / `bitdepth` — sample depth; output is saturated to
  `0 .. 2**bits - 1`. `maxval` sets the ceiling directly.
- `realedge` — explicit step size above which an across-seam jump counts as
  real detail.

For each vertical block boundary at x=kB and horizontal at y=kB:
boundary activity `A = |mean(left 2 cols) - mean(right 2 cols)|`
(absolute mean difference of the two columns/rows flanking the edge),
taken per boundary line and averaged. Measuring per line is required:
a whole-line signed difference cancels exactly on block-sized
checkerboards and reports `A = 0` for every seam, so nothing is ever
classified.

When `A >= strength`, filter the edge line by line: each pixel within
2 px of the boundary is replaced by a blend of itself and its mirror
image across the edge: `out = (2*self + across + 2) // 4` for the two
pixels immediately flanking, and `(3*self + across + 2) // 4` for the
pixels one step further out. The far taps are `pos-2` and `pos+1`; its
mirror is `pos+1` (not `pos`).

A sample is kept when it is real detail rather than blocking:

- its gradient against its inward neighbour (one step further into its
  own block) exceeds `protect` — real texture, keep it; or
- its jump to the mirror sample across the seam exceeds `realedge`
  (defaults to `protect`, capped by `qp` when present) — real contour,
  keep it. This is what stops a hard edge that lands on a block seam
  from being flattened.

Mirror pairs are decided and blended as a unit, so filtering never
introduces a step where the two sides were equal.

Pixels away from block boundaries are untouched. Boundaries at the image
rim (no mirror neighbour, or in an area where a tap has no inward
neighbour) are reported as unprocessable and are never read out of
bounds or wrapped.

Output `{"pixels":[...], "filtered":[[x,y]...], "edges":[...]}`:

- `pixels` — the filtered frame.
- `filtered` — coordinates whose value changed, sorted row-major.
- `edges` — one entry per boundary in processing order (all vertical
  left to right, then all horizontal top to bottom), with `{"at","dir",
  "A","filtered"}`: `A` rounded to 1 decimal, and `filtered` true only
  when that boundary actually changed a sample.

Tests: `python3 -m unittest test_deblock`
