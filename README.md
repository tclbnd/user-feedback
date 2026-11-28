# argokit

Frame check for the 8-bit BRP videos of Argonaut Software's mid-90s games.
The rip tool stores the video chunks of one movie as a `.brp` pack;
`argoinfo.py` decodes every chunk the way the game's player does and prints
one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `ARGOPACK` |
| 2 + 2 | width, height (`u16` little-endian, both even) |
| 2 | chunk count (`u16` little-endian) |
| | for each chunk its size (`u32` little-endian) and the video chunk exactly as stored in the movie |

Each chunk starts with a four-character tag: `PAL8` (palette update: start
and count as `u16` little-endian, then RGB triples), `MAD1` (a list of block
types ended by `0xFF`: 8 fills 8×8 blocks, 7 moves blocks, 6 is a raw
picture, 5 fills 2×2 blocks, 2 is a coded delta), `AVCF` / `ALCD` (a table
of 256 2×2 pixel blocks, then one table index per 2×2 block of the picture —
for `ALCD` a bitmask comes first and only the masked blocks get an index) and
`RLEF` / `RLED` (count and pixel pairs; a zero count skips that many pixels).
The picture is kept from chunk to chunk.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per chunk: `state` (`palette` for a palette chunk, `decoded` for a picture, `error` when the player rejects the chunk) and, for pictures, `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `argokit/argo.py` – pack reader and chunk decoders
- `argoinfo.py` – command line

## Running

    python3 argoinfo.py examples/movie.brp
    python3 -m unittest discover -s tests
