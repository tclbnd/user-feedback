# avskit

Frame check for the AVS cut-scenes of Creature Shock. The rip tool stores the
video packets of one movie as an `.avs` pack; `avsinfo.py` decodes every
frame the way the game's player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `AVSMOVIE` |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame as stored in the movie: an optional palette block followed by one video block |

Every block starts with a 4-byte header: sub-type, block type, and a 16-bit
size. A palette block (type 3) gives the first colour number and the number
of colours (`u16` little-endian each), then three 6-bit components per colour.
A video block (type 1) codes the 318 × 198 picture as vectors: key frames
(sub-type 0) and one kind of change frame use 3 × 3 vectors, the other two
kinds of change frame use 2 × 2 and 2 × 3 vectors. All frames are drawn onto
one picture and one palette, both starting as all zero.

## Output

| field | meaning |
|---|---|
| `width`, `height` | 318 and 198 |
| `frames` | one object per frame: `ok` (whether the frame decoded), `key` (key frame) and `crc32` of the picture after it as RGB bytes (three per pixel, rows top to bottom, palette colours as the game shows them), lower-case hex |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `avskit/avs.py` – block decoder
- `avsinfo.py` – command line

## Running

    python3 avsinfo.py examples/intro.avs examples/hangar.avs
    python3 -m unittest discover -s tests
