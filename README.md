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
