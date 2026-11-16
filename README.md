# Edit timecode converter

Run: `python3 timecode.py input.json output.json`

Input JSON has a `tasks` array. Each task has `id`, `fps_num`/`fps_den`
(rational frame rate), `drop_frame` (bool), `source_start_index` (integer,
frame index whose label is 00:00:00:00), and `ops` — an array of operations:

- `{"dir":"label_to_index","label":"HH:MM:SS:FF"}` → frame index
- `{"dir":"index_to_label","index":N}` → label
- `{"dir":"convert","label":"HH:MM:SS:FF","to_fps_num":X,"to_fps_den":Y,"to_drop":B}`
  → label at the other rate for the same media time

Rules: nominal fps = round(fps_num/fps_den). Drop-frame numbering skips the
first 2 labels (29.97) or first 4 labels (59.94) of every minute except
minutes divisible by 10. Non-drop numbering is continuous. A label that
references a skipped number is invalid. Indices wrap at 24 hours of media
time. `source_start_index` shifts the index basis: index S ↔ label
00:00:00:00. `convert` maps the source index to the target rate's nearest
frame by media time (half ties round up). `FF` separator is `:` for
non-drop and `;` for drop. Invalid input yields `{"valid":false,"label":null,"index":null}`.
Output: `{"tasks":[{"id":..., "ops":[{"valid":bool,"label":...,"index":...}]}]}`.
