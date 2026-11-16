# First-order Ambisonics rotator

Run: `python3 foa.py input.json output.json`

Input: `channels` in ACN order `[W, Y, Z, X]` — flat sample arrays of
equal length; `ops` applied in order, each `{"kind","deg"}` with kind in
`yaw|pitch|roll` (degrees, may be negative/non-integer); `sn3d` bool —
when true the directional channels are already SN3D normalized; when
false they are raw and gains use the FuMa-style factor listed below.

Conventions: W is omnidirectional, never touched. Right-handed
coords: X front, Y left, Z up. `yaw` rotates around Z — positive yaw
turns the scene toward the LEFT (a source dead-ahead moves to +Y):
  X' = X·cos − Y·sin ; Y' = X·sin + Y·cos ; Z' = Z.
`pitch` rotates around Y — positive pitch tilts the scene UP
(X toward +Z): X' = X·cos − Z·sin ; Z' = X·sin + Z·cos ; Y' = Y.
`roll` rotates around X — positive roll swings +Y toward +Z:
  Y' = Y·cos − Z·sin ; Z' = Y·sin + Z·cos ; X' = X.
Angles use degrees. Non-SN3D input multiplies the Y/Z/X channels by
1/sqrt(2) equivalent balance relative to W: with `sn3d:false` every
emitted W is copied raw and each directional sample is multiplied by
sqrt(2) before rotation and divided by sqrt(2) after — net effect on
pure rotations is identity on the normalization, but it must round-trip
exactly. Output `{"channels":[W,Y,Z,X]` same lengths, all values
`round(v,6)`, plus `energy`: per-channel sum of squares rounded 6 —
rotations must preserve total energy within 1e-9 relative.
