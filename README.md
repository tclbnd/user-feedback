# dsvkit

Frame check for the cut-scene videos of Delphine Software's mid-90s PC games
(the `.cin` movies). The rip tool stores the video packets of one movie as a
`.dsv` pack; `dsvinfo.py` decodes every frame the way the game's player does
and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `DSVPACK1` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the video packet exactly as stored in the movie |

A video packet starts with a palette type byte, a colour count (`u16`
little-endian) and a bitmap type byte, followed by the palette update and the
bitmap data. Bitmap types: 9 RLE, 34 RLE delta, 35 Huffman + RLE, 36 Huffman +
RLE delta, 37 Huffman, 38 LZSS, 39 LZSS delta.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the player rejects the packet) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `dsvkit/dsv.py` – pack reader, palette and bitmap decoders
- `dsvinfo.py` – command line

## Running

    python3 dsvinfo.py examples/intro.dsv examples/logo.dsv
    python3 -m unittest discover -s tests
