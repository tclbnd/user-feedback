# toonkit

Frame check for CDToons animations, the sprite-based QuickTime video used by
mid-90s Broderbund/Living Books CD-ROM titles. The rip tool stores the frames
of one movie as a `.toon` pack; `tooninfo.py` decodes every frame the way the
CDToons decompressor does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `TOONPACK` |
| 2 + 2 | width, height (`u16` big-endian) |
| 2 | frame count (`u16` big-endian) |
| | for each frame its size (`u32` big-endian) and the sample exactly as stored in the movie |

Each sample has a 44-byte header (frame number, background colour, sprite
count and offset, referenced sprite count, palette sprite and flag), a list
of referenced sprites to draw, the sprite blocks introduced in that frame,
and optional tagged entries such as `Diff` blocks. Sprite scanlines are
stored as a byte count followed by raw and run segments; colour 0 in a run
is transparent. Palettes are sprites holding 256 entries of three 16-bit
components.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the decompressor rejects the sample), `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error), and `sprites`, the number of sprite blocks still held after the frame |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `toonkit/toons.py` – pack reader, sprite store and renderer
- `tooninfo.py` – command line

## Running

    python3 tooninfo.py examples/cartoon.toon
    python3 -m unittest discover -s tests
