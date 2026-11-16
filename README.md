# Object audio automation renderer

Run: `python3 auto.py input.json output.json`

Input: `sample_rate`, `frame_samples` (render quantum), `n_frames`,
`objects`: each `{"id","points":[[t_ms,azimuth,elevation,gain]...sorted],
"interp":"linear"|"step"|"spline"}` plus `master_curve` — a global gain
automation `[[t_ms,gain]...]` that multiplies every object.

Rules: per object, sample its position at the START of each render
frame (`t = frame*frame_samples*1000/sample_rate` ms). `linear`:
straight segment between surrounding points (clamped to end values
outside the point range). `step`: hold the LAST point whose t_ms <= t
(before the first point → first point's value, not silence).
`spline`: Catmull-Rom over the 4 surrounding points (endpoints
duplicated at range edges). A point exactly ON a frame boundary
belongs to the new value (evaluate at t, not t-epsilon). Angles wrap
at ±180 — interpolate azimuth over the SHORTEST arc (a move 170→-170
covers 20°, not 340°). `gain` in dB, linear-interpolated in dB then
applied; master_curve always linear. Output per frame per object:
`{"frames":[[{"id","az","el","lin_gain"}...]...],
 "applied_master":[lin_gain per frame]}` with az/el rounded to 3
decimals and lin_gain = 10^((obj_db+master_db)/20) rounded to 6.
