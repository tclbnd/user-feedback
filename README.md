# Black/freeze detector

Run: `python3 detect.py input.json output.json`

Optional: `python3 detect.py --chunk N input.json output.json` reads the
frames in blocks of N. The result is identical for every N — the detector
keeps its run state across blocks and only emits an event once, when the run
closes.

Input: `frames` (ordered list `{"pts":int,"luma":float,"diff":float}` — mean
luma and mean abs diff vs previous decoded frame, first frame diff=0),
`windows`: `{"black_luma":float,"freeze_diff":float,"min_black_ms":int,
"min_freeze_ms":int,"gap_ms":int}` and `frame_ms` (nominal frame duration).

Rules: a frame is black if luma < black_luma; frozen if diff < freeze_diff
(diff==0 counts as frozen except the stream's first frame which is never
frozen unless it is also black — first frame counts toward black only).
Run-length group consecutive flagged frames; emit an event when a run's
duration reaches its min_*_ms; runs of the same kind separated by less
than gap_ms of unflagged frames merge into one event (boundary uses exact
<, not <=). A frame matching both classes is `both` and counts toward
whichever run it joins (a `both` frame inside a black run extends black,
inside a freeze run extends freeze); a `both` frame that opens a run (no
adjacent flagged frame) is reported as black. Output events sorted by start:
`{"kind":"black"|"freeze","start":pts,"end":pts+frame_ms*len,"frames":n,
"reason":"black"|"freeze"|"both"}` (`end` is the pts at which the run leaves
the screen; it equals `start + frame_ms*len` for constant-frame-rate input)
plus `playable` — the frame ranges NOT covered by any event, same shape with
kind "playable", and `skipped` — frame indices dropped when pts jumps
backward (pts must be non-decreasing; a frame with pts < previous pts is
skipped and reported by index).

## Frame-accurate timing

Durations are measured on the real pts timeline, not by multiplying a frame
count by `frame_ms`: a frame is displayed until the next decoded frame, so
`end` is the next frame's pts. `frame_ms` is used only for the last frame.
This makes variable-frame-rate material and dropped frames correct — a jump
in pts means the previous frame was held for the whole gap.

Merging happens before the minimum-duration check, so a long frozen picture
broken by occasional one-frame noise spikes is one event, not a burst of
short alarms. Intervals in the output are contiguous: `playable` spans
complement `events` spans exactly.

## Marked / reserved black

Optional input annotations let already-known black be excluded from alarms
(director slates, reserved black, network black). They are inert when
absent.

* per frame: `"marked_black": true` (also `intentional_black`,
  `reserved_black`, `expected_black`)
* by ranges: top-level `"marked_black": [{"start": pts, "end": pts}, ...]`
  (also `intentional_black`, `reserved_black`, `expected_black`,
  `black_ranges`, `slates`; pairs may be `[start, end]` as well)

Marked frames produce no black or freeze alarm and stay in `playable`. A run
is not extended across marked frames; marked frames are unflagged material
for the `gap_ms` merge rule.

## Tests

`python3 -m unittest discover -s tests` exercises threshold boundaries,
`both` handling, merge boundaries, VFR/dropped/backwards pts, marked black,
and compares results against an independent per-frame oracle, including the
same input read in different block sizes.

