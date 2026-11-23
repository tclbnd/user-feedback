# proreskit

Ingest check for the ProRes elementary streams (`.prores`) that the dailies
system extracts from camera files. `proresinfo.py` walks each stream frame by
frame and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `version`, `creator` | bitstream version and the four-character encoder id from the first frame |
| `width`, `height` | frame size |
| `chroma` | `4:2:2` or `4:4:4` |
| `scan` | `progressive`, `interlaced_tff` or `interlaced_bff` |
| `aspect`, `frame_rate` | from the frame header; `frame_rate` in frames per second as a fraction string (`24000/1001` for 23.976, `30000/1001` for 29.97); `null` when the code is 0 (unknown) or reserved |
| `color` | colour primaries, transfer characteristic and matrix codes |
| `alpha_bits` | 8 or 16 for streams with alpha, else `null` |
| `custom_matrices` | the frame header carries its own quantisation matrices |
| `pictures`, `slices` | pictures in a frame (2 for interlaced frames, one per field) and slices in all of them |
| `frames`, `min_frame_bytes`, `max_frame_bytes` | complete frames in the stream and their sizes |
| `inconsistent_frames` | frames whose picture sizes or slice tables do not add up to the frame size |
| `truncated` | the stream ends inside a frame |

The per-frame fields describe the first frame.

## Layout

- `proreskit/prores.py` – frames, pictures, slices
- `proresinfo.py` – command line

## Running

    python3 proresinfo.py examples/a001_c003.prores examples/graphics_4444.prores
    python3 -m unittest discover -s tests
