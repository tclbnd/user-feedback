# arbckit

Frame check for Gryphon Software ARBC screen recordings (the `ARBC` video
codec of Gryphon's screen-capture tools). The rip tool stores the packets of
one recording as an `.arbc` pack; `arbcinfo.py` decodes every packet the way
the ARBC codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `ARBCPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet exactly as stored in the AVI |

A packet has an 8-byte header, a segment count (`u16` little-endian) and the
segments. Each segment gives one colour (its red, green and blue bytes each
followed by a padding byte) and a flags byte selecting the tile sizes that
follow (1024, 256, 64, 16 and 4 pixels square); each tile group is a tile
count and, per tile, its row, column and a 16-bit mask of the 4 × 4 parts of
the tile to paint. The picture is stored bottom row first.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per packet: `state` (`decoded`, `skipped` when the packet produces no picture, `error` when the codec rejects it); decoded packets also give `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `arbckit/arbc.py` – pack reader and tile painter
- `arbcinfo.py` – command line

## Running

    python3 arbcinfo.py examples/screen.arbc
    python3 -m unittest discover -s tests
