# Edit timecode converter

Run: `python3 timecode.py input.json output.json`

Input JSON has a `tasks` array. Each task has `id`, `fps_num`/`fps_den`
(rational frame rate), `drop_frame` (bool), `source_start_index` (integer,
frame index whose label is 00:00:00:00), and `ops` — an array of operations:

- `{"dir":"label_to_index","label":"HH:MM:SS:FF"}` → frame index
- `{"dir":"index_to_label","index":N}` → label
- `{"dir":"convert","label":"HH:MM:SS:FF","to_fps_num":X,"to_fps_den":Y,"to_drop":B}`
  → label at the other rate for the same media time

Rules:

- `nominal fps = round(fps_num/fps_den)`, computed on the exact rational
  (ties to even). All index math is integer arithmetic — no floating point.
- Drop-frame numbering skips the first 2 labels (29.97) or first 4 labels
  (59.94) of every minute except minutes divisible by 10. Non-drop numbering
  is continuous. Drop-frame is only defined for those two nominal rates.
- A label that references a skipped number is invalid. Labels are validated
  against the task's rate, field ranges and separator.
- Indices wrap at 24 hours of media time: 2,589,408 frames for 29.97
  drop-frame, 5,178,816 for 59.94 drop-frame, and `nominal * 86400` for
  non-drop.
- `source_start_index` shifts the index basis: index S ↔ label 00:00:00:00.
  It is applied only on the source side; labels always name positions inside
  the 24-hour day.
- `convert` uses the source frame's media time to pick the target rate's
  nearest frame (exact halves round up), then emits the target rate's label.
  The returned `index` is the source frame index — the task defines only one
  index basis — so a local edit range survives the round trip.
- Results echo the caller's own `label`/`index` so an exported range keeps
  its input basis instead of being rewritten by the converter. Indices that
  fall outside one day wrap for label purposes but are reported as given.
- `FF` separator is `:` for non-drop and `;` for drop, on both input and
  output. A label whose separator contradicts `drop_frame` is invalid, so
  material recorded under one marking convention is never silently
  reinterpreted under the other.
- Invalid input yields `{"valid":false,"label":null,"index":null}`. A task
  with an unusable rate or `source_start_index` invalidates all of its ops.

Output: `{"tasks":[{"id":..., "ops":[{"valid":bool,"label":...,"index":...}]}]}`.

Tests: `python3 -m unittest -v test_timecode` — covers whole-day label/index
round trips for 29.97 and 59.94 drop-frame and 29.97 non-drop, tenth-minute
and hour boundaries, day wrap, source offsets, local edit ranges, tie
rounding, and rejected skipped/malformed labels.
