# spreskit

Frame check for Screenpresso screen recordings (the `SPV1` video codec of the
Screenpresso capture tool). The rip tool stores the video packets of one
recording as a `.spr` pack; `spresinfo.py` decodes every packet the way the
Screenpresso codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `SPRSPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet exactly as stored in the AVI |

A packet has two header bytes. The first carries the compression level and,
in its low bit, the key-frame flag; the second is the pixel-size code, a power
of two: `4` for RGB555 (2 bytes/pixel), `8` for BGR (3 bytes/pixel) and
`16` for BGR0 (4 bytes/pixel). The header is followed by a zlib stream holding
the frame data. Key frames carry a whole picture, other frames a delta whose
bytes the codec adds (modulo 256) to the previous picture.

Bitmaps use the Windows DIB layout: rows are stored bottom to top and each row
is padded so the stride (width times bytes-per-pixel, rounded up to a multiple
of four) divides the buffer evenly.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per packet: `state` (`decoded`, or `error` when the codec rejects the packet); decoded packets also give `key`, `bytes` (bytes per pixel) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom, 5-bit components widened to 8 bits by repeating their top bits |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `spreskit/spres.py` – pack reader and frame decoder
- `spresinfo.py` – command line

## Running

    python3 spresinfo.py examples/tutorial.spr
    python3 -m unittest discover -s tests
