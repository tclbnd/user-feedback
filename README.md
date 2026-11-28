# gdvkit

Frame check for Gremlin Digital Video (`.gdv`), the cut-scene format of
Gremlin Interactive's mid-90s PC games. The rip tool stores the video frames
of one movie as a `.gdv` pack (scaled half-resolution movies are not
included); `gdvinfo.py` decodes every frame the way the game's player does
and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `GDVPACK1` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the video chunk exactly as stored in the movie |

Each chunk starts with a 32-bit little-endian flags word: the low four bits
select the compression (0 and 1 carry a palette of 256 six-bit RGB triples,
2, 5, 6 and 8 are LZ-style modes, 3 repeats the previous frame), bits 8 and
up give a number of pixels to leave unchanged at the start of the frame.
The picture is kept in a buffer that is preceded by a 4096-byte preamble
which back references may reach into.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the player rejects the chunk) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `gdvkit/gdv.py` – pack reader, bit readers and the compression modes
- `gdvinfo.py` – command line

## Running

    python3 gdvinfo.py examples/intro.gdv
    python3 -m unittest discover -s tests
