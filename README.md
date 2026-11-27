# yopkit

Check for the YOP cut-scenes of Psygnosis games (Ecstatica, Microcosm and
others). The extraction tool stores the video frames of one movie with the
movie's palette parameters as a frame sequence (`.ysq`); `yopinfo.py`
decodes the frames in order and prints one JSON summary per file.

## File layout

`YOPSEQ` and two NULs, width and height (`u16` little-endian each, both
even), the three palette parameters from the YOP header (colours updated
per frame, first colour for even frames, first colour for odd frames, one
byte each), the frame count (`u32` little-endian), then for each frame its
length (`u32` little-endian) and the video part of the frame exactly as
stored in the YOP file.

Decoding starts from a picture of index 0 and an all-black palette. Each
frame is decoded on top of the previous picture. A frame that cannot be
decoded is reported with `ok` false; whatever it had already drawn stays.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, and `crc32` of the index picture after it (rows top to bottom, lower-case hex) |
| `colours` | distinct palette indices in the last picture |
| `rgb_crc32` | CRC-32 of the last picture with the current palette applied (R G B per pixel, 8 bits each) |

## Layout

- `yopkit/yop.py` – frame decoder
- `yopinfo.py` – command line

## Running

    python3 yopinfo.py examples/intro.ysq examples/level.ysq
    python3 -m unittest discover -s tests
