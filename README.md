# Audio playback sample planner

Run it with:

```sh
python3 planner.py input.json output.json
```

Input is UTF-8 JSON with one shared `sample_rate` and a `tracks` array. Each track has an `id`, a `frames` array, and an inclusive playable interval `[start, end]` in decoded-track sample coordinates. Each frame has a decoded `samples` count, encoder `priming`, and trailing `padding` count. Sample coordinates start at zero at the first decoded sample. A frame occupies the next `samples` positions; `priming` and `padding` describe samples to exclude from playback. An interval can begin or end within a frame, cover one frame, or contain zero playable samples (`end < start`).

The planner emits, for each track, frame-local half-open playable slices, the track's global half-open sample range in the concatenated output, and a reversible mapping between a global output sample and its source frame/local sample. Invalid ranges or metadata that cannot describe the decoded frames are reported per track without discarding other valid tracks.

## Model

`priming` and `padding` are markers on the decoded sample axis, not fields that
must fit inside one frame:

- `priming` cuts `[frame_start, frame_start + priming)`.
- `padding` cuts `[frame_end - padding, frame_end)`.

A final-frame padding larger than that frame therefore reaches back into the
preceding frame, and a first-frame priming can reach forward, so a trim may
span two frames or more. All marker intervals are merged and then subtracted
from the inclusive playable interval. Taking the union means overlapping
markers remove a sample once and adjacent markers cannot leave a one-sample
gap.

Coordinates are inclusive; `start` may be `total` and `end` may be `-1`, which
keeps empty ranges representable at either edge. `end < start` is the normal
empty-range request and contributes no samples. A start further outside the
track, or an end at/after the decoded length, is contradictory metadata and is
rejected for that track only.

Playable samples are packed back to back, so the global output clock has no
holes at track joins and empty tracks contribute nothing. `slices` use
`[start, end)` within a frame; `global_start`/`global_end` and `mapping` use
`[)` on the global clock.

## Output

```json
{
  "sample_rate": 48000,
  "samples": 9,
  "tracks": [
    {
      "id": "a",
      "slices": [{"frame": 0, "start": 1, "end": 4}],
      "samples": 3,
      "global_start": 0,
      "global_end": 3,
      "mapping": [{"output_start": 0, "output_end": 3, "frame": 0,
                   "start": 1, "end": 3}]
    }
  ],
  "mapping": [],
  "errors": [],
  "warnings": []
}
```

`mapping` is repeated at the top level with `track` and `track_index` added.
Each segment is invertible: source `local` maps to `output_start + (local -
start)` and an output sample maps back with `planner.locate`, which returns
`track`/`frame`/`sample`. Tracks whose metadata cannot describe their frames
carry `error` and contribute no output samples; the remaining tracks keep their
place on the global clock. Metadata that is describable but contradictory
(markers longer than their frame, overlapping markers, a per-track
`sample_rate` disagreeing with the shared one, duplicate ids) is listed under
`warnings` and the track still plays.

## Verification

`verify_planner.py` is an independent brute-force checker: it builds a
per-decoded-sample truth table, concatenates it as the golden audio, and
compares the planner sample by sample, including cross-frame trimming, whole
segments, frame-end jumps, empty tracks, padding spanning two frames, random
seeks, and an invertibility/no-duplicate check on the global mapping.

```sh
python3 verify_planner.py
```
