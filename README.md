# edlkit

Conform check for the edit decision lists (CMX 3600 `.edl`) that come back
from the offline edit. `edlinfo.py` reads each list and prints one JSON
summary, used before the online conform to catch events that would pull the
wrong source frames or leave holes in the cut.

## Output

| field | meaning |
|---|---|
| `title` | the TITLE line |
| `events` | one entry per event, in record order |
| `tracks` | event counts per track (`V`, `A`, `A2`, `A3`, `A4`); an event on several tracks counts on each |
| `video_gaps` | `[out, in]` record frame pairs where the picture has a hole between two video events |
| `video_overlaps` | `[in, out]` record frame ranges where a video event starts before the video event before it has ended |
| `reels` | source reels the list uses, sorted, outgoing sides of transitions included; black (`BL`, `BLK`, `BLACK`) and the auxiliary source `AX` are not reels |
| `black_events` | events that cut or dissolve to or from black |
| `record_frames`, `record_seconds` | length of the cut from the first record in to the last record out; seconds as a fraction string |

Each event has `event` (number), `reel`, `tracks` (the tracks it is on:
`AA` is `A` and `A2`, `B` is `V` and `A`, `AA/V` is all three), `transition`
(`C`, `D` or `W`), `transition_frames`, `source_in`, `source_out`,
`record_in`, `record_out` (frame counts from 00:00:00:00), `duration` (record
frames), `clip` (the clip name from the list's comments, `null` without one),
`speed` (the motion effect speed in frames per second as a fraction string,
`null` without one), `source_frames` (source frames the event uses: at the
motion effect speed when there is one) and, for dissolves and wipes,
`from_reel` and `from_clip` of the outgoing side (else `null`).

Timecode follows the list's FCM lines (drop frame or non-drop frame);
`--fps` gives the frame rate (24, 25, or 30 for 29.97).

## Layout

- `edlkit/edl.py` – lines, events, timecode
- `edlinfo.py` – command line

## Running

    python3 edlinfo.py examples/episode.edl examples/promo.edl
    python3 -m unittest discover -s tests
