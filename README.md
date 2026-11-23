# cplkit

Check for the IMF packages delivered to the streaming platform: each package's
Composition Playlist (CPL, SMPTE ST 2067-3) is read before ingest.
`cplinfo.py` prints one JSON summary per CPL, used to compare running times,
track files and markers with the delivery order.

## Output

| field | meaning |
|---|---|
| `title`, `edit_rate` | ContentTitle and the composition edit rate (fraction string) |
| `timecode_start` | TimecodeStartAddress of the CompositionTimecode, `null` without one |
| `segments` | segments in the composition |
| `duration_units`, `duration` | composition length in composition edit units and in seconds (fraction strings); each segment counts the length of its main image sequence |
| `sequences` | per sequence kind (element name, any namespace): `resources` and `duration_units` over all segments |
| `track_files` | distinct track files the composition uses |
| `markers` | each marker's `label`, `offset` on the composition timeline (composition edit units, fraction string) and `timecode`: the CompositionTimecode start address plus the whole edit units of the offset, counted at the TimecodeRate with the drop-frame rule when TimecodeDropFrame is true, written `HH:MM:SS:FF` (`;` before the frames for drop-frame); `null` without a CompositionTimecode |
| `content_kind` | ContentKind (`feature`, `trailer`, `episode`, ...), `null` without one |
| `tracks` | per sequence kind (marker sequences included), the number of distinct virtual tracks (TrackId) over all segments |
| `mismatched_segments` | numbers (from 1) of segments whose sequences do not all have the same length |

A resource plays SourceDuration edit units from EntryPoint, RepeatCount
times. Missing values take their defaults: EntryPoint 0, SourceDuration the
rest of the track file after the entry point, RepeatCount 1, EditRate the
composition's. Resource durations are counted in the resource's own edit rate
and converted to the composition's. Both the 2013 and 2016 CPL schemas are in
use.

## Layout

- `cplkit/cpl.py` – XML, segments, sequences, resources, markers
- `cplinfo.py` – command line

## Running

    python3 cplinfo.py examples/CPL_feature.xml examples/CPL_trailer.xml
    python3 -m unittest discover -s tests
