# Black/freeze detector

Run: `python3 detect.py input.json output.json`

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
inside a freeze run extends freeze). Output events sorted by start:
`{"kind":"black"|"freeze","start":pts,"end":pts+frame_ms*len,"frames":n,
"reason":"black"|"freeze"|"both"}` plus `playable` — the frame ranges NOT
covered by any event, same shape with kind "playable", and `skipped` —
frame indices dropped when pts jumps backward (pts must be non-decreasing;
a frame with pts < previous pts is skipped and reported by index).
