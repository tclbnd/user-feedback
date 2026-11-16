# Bayer demosaic + bad pixel correction

Run: `python3 bayer.py input.json output.json`

Input: `pattern` ∈ {RGGB, BGGR, GRBG, GBRG} (color site of pixel (y,x):
RGGB → even/even=R, even/odd=G, odd/even=G, odd/odd=B; BGGR swaps R↔B;
GRBG → even/even=G, even/odd=R, odd/even=B, odd/odd=G; GBRG mirrors
GRBG), `width`,`height`,`raw` (h×w ints), `bad_pixels` ([[y,x]...]),
`black_level`,`white_level`. Optional `crop_origin` ([y,x], or
`origin_y`/`origin_x`) gives the sensor coordinate of the cropped frame's
pixel (0,0): the CFA phase is shifted by its parity, so a crop that starts
at an odd row/column keeps the right sampling layout. Omitting it is
equivalent to [0,0]. `black_level`/`white_level` may be a scalar, `[r,g,b]`,
or `{"R":…,"G":…,"B":…}`; black level is subtracted per channel before
scaling and samples are clamped at both ends.

Rules: (1) correct each bad pixel to the MEDIAN of its same-color sites
at Chebyshev distance 2 — positions (y±2,x), (y,x±2), (y±2,x±2) that are
in bounds (diagonal offsets count for R/B, and for G the same-parity
sites at (±2,0),(0,±2),(±2,±2) that are G); fewer than 3 in-bounds
same-color neighbors → average of whatever exists; zero → keep raw and
list under `uncorrectable`. (2) bilinear demosaic: for each pixel emit
[r,g,b] in sensor units — a site's own channel is its (corrected) value;
the other two channels are the mean of in-bounds same-channel orthogonal
neighbors, falling back to diagonals only at borders when no orthogonal
neighbor exists for that channel (e.g. R at a G-in-B-row site uses
horizontal R neighbors; at image left edge use only right neighbor;
R at a B site uses the up-to-4 diagonal R sites). (3) normalize each
channel `round(clamp(v-black,0,white-black)*255/(white-black))`.
Output `{"rgb":[flattened [r,g,b]...], "corrected":[[y,x,old,new]...]
(in input order), "uncorrectable":[[y,x]...]}`.

Diagnostics: `python3 bayer.py in.json out.json --diag diag.json` also
writes the unprocessed frames (`raw_original`, `raw_corrected`, the
`bad_mask`/`corrected_mask`/`uncorrectable_mask` defect maps, the
un-normalised `demosaic_sensor`, and the effective CFA `cfa` plus
`black_level`/`white_level` used). The main output document is unchanged.

Tests: `python3 tests.py` (colour bars, slanted edge, ramp, single bad
pixel, dense edge defects; all four patterns and odd crop origins; every
pixel compared against an independent reference implementation).
