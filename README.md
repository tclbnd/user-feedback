# Spectral repair across masked bins

Run: `python3 repair.py input.json output.json`

Input `{"frames":[{"t","mags":[...],"mask":[...]}...],"order":K}` —
per-frame magnitude spectrum; `mask` has 1 where the bin is
damaged and must be repaired.

Repair rule (per frame, per masked contiguous run):
- For a masked run [a..b], left context = nearest unmasked bin
  before a, right context = nearest unmasked bin after b.
- If both exist: linear interpolation in LOG magnitude:
  `mags[i] = exp( l + (r-l)*(i-(a-1))/((b+1)-(a-1)) )`.
- If only left exists: hold `mags[a-1]` across the run (EDGE hold).
- If only right exists: hold `mags[b+1]`.
- If neither: leave zeros.
- Then smooth each repaired frame's spectrum with a centred moving
  average of length `2*order+1` (zero-pad edges), applied ONLY to
  previously-masked bins.
Output `{"frames":[{"t","mags":[...]}...]}` mags rounded 5 decimals.
