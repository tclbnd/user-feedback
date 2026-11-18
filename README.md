# Adaptive deblocking filter

Run: `python3 deblock.py input.json output.json`

Input `{"w","h","pixels":[rows of ints],"block":B,"strength":S,
"protect":P}` — 8-bit luma grid, block edges every B pixels.

For each vertical block boundary at x=kB and horizontal at y=kB:
boundary activity `A = |mean(left 2 cols)| - |mean(right 2 cols)|`
(absolute mean difference of the two columns/rows flanking the edge).
When `A >= strength`, filter the edge: each pixel within 2 px of the
boundary is replaced by a blend of itself and its across-edge
neighbour: `out = (2*self + across + 2) // 4` for the two pixels
immediately flanking, and `(3*self + across + 2) // 4` for the pixels
one step further — UNLESS the pixel's own gradient against its
inward neighbour exceeds `protect` (real texture, keep it).

Pixels away from block boundaries are untouched. Output
`{"pixels":[...], "filtered":[[x,y]...]}` — the coordinates whose
value changed (sorted row-major), and `{"edges":[{"at","dir","A"}]}`
per boundary reporting its activity A rounded to 1 decimal.
