# Bayer demosaic + bad pixel correction

Run: `python3 bayer.py input.json output.json`

Input: `pattern` ∈ {RGGB, BGGR, GRBG, GBRG} (color site of pixel (y,x):
RGGB → even/even=R, even/odd=G, odd/even=G, odd/odd=B; BGGR swaps R↔B;
GRBG → even/even=G, even/odd=R, odd/even=B, odd/odd=G; GBRG mirrors
GRBG), `width`,`height`,`raw` (h×w ints), `bad_pixels` ([[y,x]...]),
`black_level`,`white_level`.

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
