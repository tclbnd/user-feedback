# c93kit

Frame check for the C93 cut-scenes of Interplay's mid-90s CD-ROM games. The
rip tool stores the video packets of one movie as a `.c93` pack; `c93info.py`
decodes every frame the way the game's player does and prints one JSON
summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `C93VIDEO` |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the video packet as stored in the movie |

Each packet starts with a flag byte (bit 0: a 256-colour palette of 8-bit
red, green, blue triples follows the picture data; bit 1: key frame), then
codes the 320 × 192 picture as 8 × 8 blocks, left to right and top to
bottom. Block types come four bits at a time from type bytes in the stream.
The player keeps two pictures and alternates between them, so a frame is
drawn over the picture from two frames back, and blocks may copy from the
previous frame. A frame without a palette shows the previous frame's
palette. Both pictures start as all zero bytes, the palette as all black.

## Output

| field | meaning |
|---|---|
| `width`, `height` | 320 and 192 |
| `frames` | one object per frame: `ok`, `key`, and `crc32` of the decoded frame as RGB bytes (three per pixel, rows top to bottom), lower-case hex; frames that fail to decode give `ok` false and `null` for the other fields |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `c93kit/c93.py` – block decoder
- `c93info.py` – command line

## Running

    python3 c93info.py examples/intro.c93 examples/map.c93
    python3 -m unittest discover -s tests
