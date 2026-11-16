# VUI colour-attribute resolver

Run: `python3 vui.py input.json output.json`

Input `{"segments":[...], "queries":[...]}`. Each segment
`{"id","frames":N,"vui":{...}|null,"container":{...}}` plays in
order; `vui` may carry any subset of `range`, `primaries`,
`transfer`, `matrix`; `container` carries the same optional keys
plus `override` (bool — container claims precedence).

Resolution PER FRAME, per field independently:
1. vui value when present,
2. else container value when present,
3. else the field's value INHERITED from the previous resolved
   frame state,
4. else `"unspecified"`.
Exception: when `container.override` is true, step 2 beats step 1
for fields the container actually declares.
A segment only affects its own frames; a later segment's missing
fields keep whatever was last resolved (they are NOT reset).
Output `{"frames":[{"seg","n","range","primaries","transfer",
"matrix","source"}...]}` where `source` maps each field to
`vui` | `container` | `inherited` | `default`. `queries`:
`{"at":frame_index}` — replay segments as if seeking to that
global frame index via the segment containing it (load that
segment's config with NO prior state) and report the resolved
record for that frame as `{"at", <record-fields>}`.

Tests: `python3 test_vui.py` (no VUI, conflicting container marks,
partial updates, two-track mixes, seeks, fixed-patch conversion).
