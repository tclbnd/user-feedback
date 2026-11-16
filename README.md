# Broadcast cue marker resolver

Run: `python3 cues.py input.json output.json`

Input `{"programs":[...], "resume":[...]}` — each program
`{"id","components":[{"id","time_base"}...],"cues":[...]}`; a cue is
`{"id","component","type","at"?,"version","target"?}` with type:
- `immediate`: cut lands at position `at` in program time.
- `at`: cut lands at program time `at` on the named component.
- `per-component`: cut lands at `at` in EVERY component's local
  time_base; emit one cut per component (`component` field ignored).
- `cancel`: removes the cue whose id == `target` in the SAME program
  (and same component scope: a cancel on component X only affects
  cues bound to component X or to all components); never touches
  other programs even when ids repeat across programs.

`version`: when two cues in the same program share `id` AND
`component` (per-component cues share with any component-specific
cue of that id), the higher `version` replaces the lower one;
equal versions keep the FIRST occurrence. Revisions never cross
program boundaries. `time_base` maps component-local times to
program time by addition.

Output `{"programs":{pid:{"cuts":[{"cue","component","at",
"state","version"}...] sorted by (at,component,cue)}}}` with
`state` = `active` | `cancelled` | `superseded` (a superseded cut
is still listed with its losing version). `resume`: list of
`{"program","from"}` — report `{"resume":[{"program","from","cuts":
[cue ids active at or after `from`, sorted]}...]}` using only
active cuts.
