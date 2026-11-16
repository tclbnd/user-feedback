# DTMF event detector + stereo mute

Run: `python3 dtmf.py input.json output.json`

Input: `samples` = `[left, right]` int arrays, `sample_rate`, `tone_ms`
(min key duration in ms — hits shorter than a full `tone_ms` window are
ignored), `threshold`, `fade` (edge ramp length in samples). DTMF matrix:
rows 697/770/852/941 Hz × cols 1209/1336/1477/1633 Hz, keys
"123A456B789C*0#D".

Rules: slide a `tone_ms` window (hop = half window) over EACH channel
independently. A window hits key K when K's row and col each carry the
max energy in their group and both energies >= `threshold` (single
frequency alone must NOT hit). A valid event needs the SAME key on BOTH
channels; the two channels may be offset by up to `tone_ms` samples —
merge same-key hits whose gap (per channel) is < `tone_ms` samples into
one event. Each channel is silenced over ITS OWN detected span, so a
delayed tone leaves no residue. Output length equals input length; to
avoid audible clicks, apply a half-cosine ramp of `fade` samples at each
event boundary (down-ramp on the `fade` samples before the span,
up-ramp on the `fade` samples after). Events:
`{"key","start","end","confidence"}` (start/end = union of both channel
spans; confidence = min over both channels of min(row,col)/threshold,
rounded to 3 decimals). `twist` = per-key max col/row energy ratio.
