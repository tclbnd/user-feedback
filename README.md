# tgvkit

Frame check for Electronic Arts TGV videos (the `.tgv` cut-scenes of several
EA PC titles). The rip tool stores the video chunks of one movie as a `.tgv`
pack; `tgvinfo.py` decodes every chunk the way EA's player does and prints
one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `TGVPACK1` |
| 2 | chunk count (`u16` little-endian) |
| | for each chunk its size (`u32` little-endian) and the chunk exactly as stored in the movie (8-byte tag and size preamble included) |

`kVGT` chunks are intra frames: a 12-byte header (width, height, palette
size as `u16` little-endian), the palette as RGB triples and the picture
compressed with EA's LZ scheme (a short header with the output size, then
codes that mix literal bytes and back references). All other chunks are
inter frames built from 4 × 4 blocks: motion vectors into the previous
frame, raw blocks and packed four-colour blocks, selected by a vector index
per block.

## Output

| field | meaning |
|---|---|
| `frames` | one object per chunk: `state` (`decoded`, `skipped` when the player drops the chunk, `error` when it rejects it); decoded chunks also give `width`, `height` and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `tgvkit/tgv.py` – pack reader, intra unpacker and inter decoder
- `tgvinfo.py` – command line

## Running

    python3 tgvinfo.py examples/intro.tgv
    python3 -m unittest discover -s tests
