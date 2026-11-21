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

## Conformance notes

- Element sizes may be unknown (all value bits set), both for the Segment and
  for Clusters/BlockGroups written by live muxers (RFC 8794 §4.3). An
  unknown-sized Cluster ends at the first element that is not one of its
  children (RFC 9559 §5.1.3), so trailing clusters, Cues, Tags, or a following
  Segment are not swallowed.
- Block timestamps are `Cluster Timestamp + Block relative timestamp`, where
  the relative timestamp is signed (audio with codec delay can start before
  its cluster's timestamp).
- All timestamps and the Duration are expressed in `TimestampScale` ticks,
  which default to 1 000 000 ns (1 ms) but may differ (e.g. editors use
  10 ms); `duration_ms` and `first_ms`/`last_ms` convert with the scale.
- Lacing: no lacing, Xiph, fixed-size (`ceil`/`floor` balanced so the sizes
  add up), and EBML lacing, where the first size is unsigned and the rest are
  biased signed differences (RFC 9559 §5.1.3).
- A SimpleBlock is a keyframe when its keyframe flag is set; a Block inside a
  BlockGroup is a keyframe when the group has no `ReferenceBlock`.
- `bytes` counts frame payloads after undoing `ContentEncodings` in reverse
  order (zlib, bzip2, header stripping).


## Layout

- `mkvkit/ebml.py` – variable-length integers and element headers
- `mkvkit/mkv.py` – segment, tracks, clusters, blocks
- `mkvinfo.py` – command line

## Running

    python3 mkvinfo.py examples/meeting.webm examples/edit.mkv
    python3 -m unittest discover -s tests
