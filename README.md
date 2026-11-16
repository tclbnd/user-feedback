# Speaker label remap

Run: `python3 remap.py input.json output.json`

Input JSON: `target_speakers` (ordered speaker ids of the playback layout),
`segments` (time-ordered, non-overlapping), each with `start`, `end`
(sample range, half-open), `source_speakers` (the explicit, ordered speaker
ids the container/decoder reports for that segment — track layout is read
from these labels, never inferred from slot order) and `samples` (array of
channel arrays, parallel to source_speakers, each a list of ints).

Rules — mapping is built per segment strictly from speaker-id identity; ids
are never sorted or positionally guessed (a remux can deliver the LFE label
in a rear-surround slot, which alphabetical alignment swaps silently):

- output channel position p takes the source channel whose speaker id
  equals `target_speakers[p]`;
- if no source carries that id, the output is silence filled at the
  *target* position (map entry `-1`) and the id is listed in `missing`;
- duplicate speaker ids inside one segment make it `conflict`: the mapping
  is ambiguous, the whole segment is muted and the duplicated ids are
  listed in `duplicates`;
- a source layout that matches no target position at all is marked
  `unknown` (all outputs `-1`, whole segment muted); the foreign labels are
  retained in `source_speakers` so it is reported, not guessed.

Layout switches are handled per time segment: each segment is remapped
independently against the current target layout, then concatenated, so
silence fill always lands on the target position of the active playback
layout and sample identity is preserved across every switch point.

Output:

```json
{
  "channels": [[ "...sample values per target position, concatenated..." ]],
  "segment_report": [
    {
      "start": 0, "end": 8,
      "status": "ok | conflict | unknown",
      "map": [0, 1, -1],
      "missing": ["LFE"],
      "duplicates": [],
      "source_speakers": ["L", "R", "C", "Ls", "Rs"]
    }
  ]
}
```

## Tests

- `python3 make_fixtures.py` — regenerates `fixtures/` (each source channel
  carries a globally unique pulse so routing is traceable);
- `python3 test_acceptance.py` — acceptance suite: independent pulses,
  duplicate-label conflict, device target-layout changes (stereo/5.1/7.1),
  same-frame conflicting metadata, missing-channel reasons, unknown-layout
  reporting and sample identity at every switch point;
- `python3 regression_compare.py` — runs the pre-fix implementation from
  git HEAD on the same fixture to demonstrate the old misrouting.

