# Shot-boundary detector

Run: `python3 shots.py input.json output.json`

Input `{"frames":[{"t","hist"}...],"thresholds":{...}}` — frames in
capture order, `t` REAL timestamp in ms (variable frame rate —
never assume fixed spacing), `hist` a 64-bin colour histogram
(floats, sums to 1). `thresholds`: `{"hard","grad","flash","merge"}`.

A long capture may also be supplied as several input files:

    python3 shots.py part1.json part2.json part3.json output.json

Each part carries `frames` (a flat list of frames or a list of chunks);
`thresholds` may be given once, in any part. Parts are concatenated and
scanned as one stream, so the output is identical to the unsplit run.
Frames may be in a list, in nested lists, or in `{"frames": [...]}`
objects to any depth.

Per adjacent pair compute `d = sum(|a-b|) / 2` (histogram distance,
range 0..1). Rules:

- `d >= hard`: hard cut — boundary AT the second frame's `t`.
- `hard > d >= grad` on `k >= 2` CONSECUTIVE pairs with the total
  accumulated change `>= hard`: a gradual transition — report the
  run's start and end `t` (`gradual` type, `frames` = run length).
  A run stops at the first pair outside `[grad, hard)`, so a hang or a
  cut after the fade is not absorbed into the transition.
- A single pair with `d >= hard` that is immediately preceded AND
  followed (within `flash` ms on both sides) by returns to a hist
  closer than `grad` to the frame before the spike is a FLASH —
  suppress it, merging the two sides (no boundary emitted).
- Boundaries closer than `merge` ms to a previous emitted boundary
  are dropped (first wins).

The `flash` window is measured in REAL milliseconds, never in frames:
a flare that stays up for several frames is a flash if the picture comes
back within `flash` ms, and the same number of frames at a low frame
rate is a real transition. The flash decision is made only after the
recovery frame has been read, so it does not depend on input chunking.

Output `{"boundaries":[{"t","type":"hard"|"gradual","confidence",
"span"?,"frames"?}...], "flashes":[{"t"}...]}`. `confidence` = d/hard
capped at 1.5 rounded to 3 decimals; `span` = [start_t,end_t] for
gradual; `frames` (gradual only) = number of frames the run covers, so
`k` pairs report `k + 1`. `flashes[].t` is the timestamp of the excursion
frame furthest from the surrounding picture.
