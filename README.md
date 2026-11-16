# Fragmented MP4 timeline rebuild

Run: `python3 frag.py input.json output.json`

Input: `tracks`: each `{"id","timescale","fragments":[...arrival order...]}`.
Each fragment `{"bmdt":int,"samples":[{"duration","size"}]}` — bmdt is the
base media decode time IN TIMESCALE UNITS of the fragment's first sample;
fragments arrive out of order and may overlap (an EDIT revising an
earlier range wins by later `seq` field value when `seq` present, else
arrival order). `targets`: `{"track","t_ms"}` playback-position probes.

Rules: per track sort samples by decode time (bmdt + cumulative duration
of preceding samples in the same fragment) — NOT by arrival order.
Overlapping samples (same decode time) from a higher-`seq` fragment
replace the lower one; identical bmdt fragments with no seq keep last
arrival. Build `timeline` = merged sorted sample list `{"pts","dur",
"size"}` in timescale units plus `gaps`: decode-time holes with no
sample (a gap needs its own entry `{"pts","dur"}` covering the hole;
holes at stream end are NOT gaps). For each target find the sample
active at t_ms — convert ms→timescale by `t*timescale/1000` truncating
to int; a probe inside a gap or past the last sample reports
`{"found":false,"nearest":pts of next sample or null}` while others
still resolve. Output `{"tracks":[{"id","timeline":[...],"gaps":[...]}],
"answers":[{"track","t_ms","found":bool,"pts","dur","size"}...]}` in
request order, plus `duration_ms` per track = (last pts+dur)/timescale.
