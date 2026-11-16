# Shot-boundary detector

Run: `python3 shots.py input.json output.json`

Input `{"frames":[{"t","hist"}...],"thresholds":{...}}` — frames in
capture order, `t` REAL timestamp in ms (variable frame rate —
never assume fixed spacing), `hist` a 64-bin colour histogram
(floats, sums to 1). `thresholds`: `{"hard","grad","flash","merge"}`.

Per adjacent pair compute `d = sum(|a-b|) / 2` (histogram distance,
range 0..1). Rules:

- `d >= hard`: hard cut — boundary AT the second frame's `t`.
- `hard > d >= grad` on `k >= 2` CONSECUTIVE pairs with the total
  accumulated change `>= hard`: a gradual transition — report the
  run's start and end `t` (`gradual` type, `frames` = run length).
- A single pair with `d >= hard` that is immediately preceded AND
  followed (within `flash` ms on both sides) by returns to a hist
  closer than `grad` to the frame before the spike is a FLASH —
  suppress it, merging the two sides (no boundary emitted).
- Boundaries closer than `merge` ms to a previous emitted boundary
  are dropped (first wins).

Output `{"boundaries":[{"t","type":"hard"|"gradual","confidence",
"span"?}...], "flashes":[{"t"}...]}`. `confidence` = d/hard capped
at 1.5 rounded to 3 decimals; `span` = [start_t,end_t] for gradual.
