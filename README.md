# dxakit

Check for the DXA cut-scene video the team pulls out of adventure games that
use it (Broken Sword, Feeble Files and others played through ScummVM). The
extraction tool stores the video frames of one movie as a frame sequence
(`.dxs`); `dxainfo.py` decodes them in order and prints one JSON summary per
file.

## File layout

`DXASEQ` and two NULs, width and height (`u16` little-endian each, both
multiples of 4), the number of frames (`u32` little-endian), then for each
frame its length (`u32` little-endian) and the frame exactly as stored in
the movie, including an optional palette in front of it.

Decoding starts with no reference picture and an all-black palette. A frame
that cannot be decoded is reported with `ok` false and leaves the reference
picture as it was.

## Compression modes

Modes 2/4 are full-frame key pictures and 3/5 full-frame deltas (one palette
index per pixel); a zero index in a delta leaves the reference pixel alone.

Modes 12 (key) and 13 (delta) are the ScummVM block encodings. The picture is
split into 4x4 blocks. After zlib inflation each block frame starts with a
12-byte big-endian header followed by 32 bytes of RLE block-type codes (a byte
under 32 is one type; a byte of 32 or more repeats the next byte `b-31`
times), then the block payloads:

| type | meaning | payload |
|---|---|---|
| 0 | skip, keep current pixels | none |
| 1 | raw block | 16 palette indices |
| 2 | two colours | 2 colours + four 1-bit row masks |
| 3, 8 | solid fill | one palette index |
| 4 | three colours | 3 colours + 16 index bytes (index 3 = keep current pixel) |
| 5, 10-15, 33 | keyframe pattern blocks | a short palette cycled over the 16 pixels |

A `NULL` frame carries no picture and keeps the previous frame. A palette
(`CMAP`) precedes some frames and stores each entry as R, G, B bytes.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, `key` (whether the frame stands on its own), and `crc32` of the 8-bit palette-index picture after it (rows top to bottom, lower-case hex) |
| `colours` | distinct palette indices in the last picture |
| `rgb_crc32` | CRC-32 of the last picture with the current palette applied (R G B per pixel) |

## Layout

- `dxakit/dxa.py` – frame decoder
- `dxainfo.py` – command line

## Running

    python3 dxainfo.py examples/intro.dxs examples/talk.dxs
    python3 -m unittest discover -s tests
