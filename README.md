# Film-grain synthesis over luma blocks

Run: `python3 grain.py input.json output.json`

Input `{"luma":[[row]...],"params":{"seed":S,"scale":A,
"cutoff":C,"strength":Q},"blocks":N}` — 8-bit luma plane;
synthesize grain and add per pixel.

Grain field: generated per 8x8 block from an AR(1) model on a
gaussian RNG seeded `S` (LCG: `x=(x*1103515245+12345)%2**31`,
normal via Box-Muller over successive draws). For each block
(row-major, index b), the DC term is
`dc[b] = 0.8*dc[b-1] + 0.6*g(b)` where g(b) is the b-th gaussian;
grain value per pixel in block = `dc[b] * A * min(1, luma/255*Q/10)`
— strength scales with pixel luma (grain fades in blacks).
Final pixel = `clamp(round(luma + grain), 0, 255)`.
Blocks run row-major; trailing partial blocks are processed with
their own dc index. Rows/cols need not be multiples of 8.
Output `{"luma":[[...]], "dc":[...]}` — dc list = per-block dc
values rounded 4 decimals.
