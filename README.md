# mkvkit

Inspector for the recording pipeline (WebM from the browser recorder and the
live ingest, Matroska from the editors). `mkvinfo.py` prints one JSON summary
per file: segment info, tracks and per-track block statistics.

## Output

| field | meaning |
|---|---|
| `timestamp_scale` | nanoseconds per timestamp tick |
| `duration_ms` | segment duration in milliseconds (rounded to 3 decimals), `null` if absent |
| `clusters` | number of clusters |
| `tracks` | one entry per track, ordered by track number |

Per track: `number`, `type`, `codec`, `enabled`, `default`, `language`
(BCP 47 tag when the track has one, otherwise the ISO 639-2 code), `blocks`
(SimpleBlocks and BlockGroups),
`frames` (frames after lacing), `keyframes` (blocks that are keyframes),
`bytes` (frame bytes after undoing the track's content encodings), `first_ms` / `last_ms` (smallest and largest
block timestamp in milliseconds, as a fraction string).

## Layout

- `mkvkit/ebml.py` – variable-length integers and element headers
- `mkvkit/mkv.py` – segment, tracks, clusters, blocks
- `mkvinfo.py` – command line

## Running

    python3 mkvinfo.py examples/meeting.webm examples/edit.mkv
    python3 -m unittest discover -s tests
