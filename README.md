# DTMF event detector + stereo mute

Run: `python3 dtmf.py input.json output.json`

Input: `samples` = `[left, right]` int arrays, `sample_rate`, `tone_ms`
(min key duration in ms — hits shorter than a full `tone_ms` window are
ignored), `threshold`, `fade` (edge ramp length in samples). DTMF matrix:
rows 697/770/852/941 Hz × cols 1209/1336/1477/1633 Hz, keys
"123A456B789C*0#D".

Rules: slide a `tone_ms` window (hop = quarter window, so a tone at least
one window long can never fall between windows) over EACH channel
independently. A window hits key K when K's row and col each carry the
max energy in their group, both energies >= `threshold`, both are
narrow-band (one analysis bin away must lose most of the energy) and the
two bins together hold a large fraction of the window's total energy
(`(row+col) / (0.5*N*sum(x^2))`, ~1.0 for a real pair). Single frequency
alone must NOT hit, and neither must broadband speech that happens to sit
on a row/col pair. A valid event needs the SAME key on BOTH channels; the
two channels may be offset by up to `tone_ms` samples — merge same-key
hits whose gap (per channel) is < `tone_ms` samples into one event.

Event boundaries are refined past the coarse window bounds: a ~20 ms
envelope is tracked inside the run, the ramp of each component is fitted
and extrapolated to zero amplitude, and the run is clamped to the real
tone extent. The minimum-duration rule is applied to that refined span,
so a 30 ms tone with `tone_ms` = 40 is rejected while a 40 ms tone is
kept.

Each channel is silenced over ITS OWN detected span, so a delayed tone
leaves no residue and the other channel is untouched while only one
channel is sounding. Output length equals input length; to avoid audible
clicks, apply a half-cosine ramp of `fade` samples at each event boundary
(down-ramp on the `fade` samples before the span, up-ramp on the `fade`
samples after; the last ramped sample is already at ~0 gain). Events:
`{"key","start","end","confidence"}` (start/end = union of both channel
spans; confidence = min over both channels of min(row,col)/threshold,
rounded to 3 decimals). `twist` = per-key max col/row energy ratio.

`python3 acceptance.py` runs the synthetic acceptance suite (paired and
single frequencies, gaps, channel delay, speech neighbours, sample rates,
window lengths, fades, block splitting, click-freeness and per-symbol
mute extents).
