# Audio playback sample planner

Run the starter with:

```sh
python3 planner.py input.json output.json
```

Input is UTF-8 JSON with one shared `sample_rate` and a `tracks` array. Each track has an `id`, a `frames` array, and an inclusive playable interval `[start, end]` in decoded-track sample coordinates. Each frame has a decoded `samples` count, encoder `priming`, and trailing `padding` count. Sample coordinates start at zero at the first decoded sample. A frame occupies the next `samples` positions; `priming` and `padding` describe samples to exclude from playback. An interval can begin or end within a frame, cover one frame, or contain zero playable samples (`end < start`).

The planner should emit, for each track, frame-local half-open playable slices, the track's global half-open sample range in the concatenated output, and a reversible mapping between a global output sample and its source frame/local sample. Invalid ranges or metadata that cannot describe the decoded frames should be reported per track without discarding other valid tracks.

This starter intentionally applies priming and padding only when they fit in one frame and does not build a complete reversible mapping. The independent verifier covers cross-frame trimming, single-frame and empty ranges, and track-local invalid metadata while confirming valid tracks remain playable.
