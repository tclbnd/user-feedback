# cramkit

Frame check for Microsoft Video 1 (CRAM) clips taken from old Windows games
and AVI-based cut-scenes. The capture tool stores the video chunks of one
stream in a frame pack (`.cram`); `craminfo.py` decodes every frame in order
and prints one JSON summary per file.

## File layout

`CRAMPAK` and a NUL, the width and height (`u16` little-endian each), the
depth (8 for palettised, 16 for RGB555), the frame count (`u16`
little-endian), then for each frame its length (`u32` little-endian) and the
video chunk exactly as stored in the AVI.

All frames are decoded onto one picture in order, as a player does. The
picture starts out all zero before the first frame.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `depth` | stream parameters |
| `frames` | one object per frame, in order |

Each frame object has:

| field | meaning |
|---|---|
| `crc32` | CRC-32 (lower-case hex) of the whole picture after the frame, rows top to bottom: one palette index per pixel for depth 8, the 15-bit colour as `u16` little-endian per pixel for depth 16 |
| `skip`, `fill`, `two`, `quad` | how many 4×4 blocks of the frame were skipped, filled with one colour, drawn with two colours, or drawn with eight colours |
| `short` | `true` when the chunk ends before every block of the frame has been coded |

## Layout

- `cramkit/cram.py` – block decoder
- `craminfo.py` – command line

## Running

    python3 craminfo.py examples/intro.cram examples/menu.cram
    python3 -m unittest discover -s tests
