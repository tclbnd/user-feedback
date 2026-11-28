# sgrkit

Frame check for Silicon Graphics RLE video (the 8-bit `RLE` codec of SGI
movie files and Indigo video captures). The rip tool stores the frames of
one clip as an `.sgr` pack; `sgrinfo.py` decodes every frame the way the SGI
RLE decoder does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `SGRLPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame exactly as stored in the movie |

A frame is a series of opcode bytes, each followed either by one pixel to
repeat or by a span of raw pixels. Pixels are 8-bit RBG 3:2:3 (red in the
top three bits, then two bits of blue, then three bits of green).

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the decoder rejects the frame); decoded frames also give `crc32` (lower-case hex) of the picture as BGR8 bytes (blue in the top two bits, then three bits of green, then three bits of red), one byte per pixel, rows top to bottom, and `colours`, the number of distinct pixel values |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `sgrkit/sgr.py` – pack reader and frame decoder
- `sgrinfo.py` – command line

## Running

    python3 sgrinfo.py examples/studio.sgr
    python3 -m unittest discover -s tests
