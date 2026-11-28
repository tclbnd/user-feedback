# pafkit

Frame check for Packed Animation File videos (`.paf`), the cut-scene format of
several mid-90s PC games such as Hi-Octane. The rip tool stores the video
frames of one movie as a `.paf` pack; `pafinfo.py` decodes every frame the way
the game's player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `PAFPACK1` |
| 2 + 2 | width (always 256), height (`u16` little-endian, a multiple of 4) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the video frame exactly as stored in the movie |

A frame starts with a code byte: the low four bits give the frame type
(0 block copy with mask opcodes, 1 uncompressed, 2 copy a stored page,
4 run-length), 0x10 aligns the preset blocks of a type 0 frame, 0x20 marks a
key frame and 0x40 a palette update (start index, count − 1, six-bit RGB
triples). The player keeps four frame pages of 256 × 256 bytes and decodes
each frame into the next page in turn.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the player rejects the frame) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `pafkit/paf.py` – pack reader, frame pages and frame types
- `pafinfo.py` – command line

## Running

    python3 pafinfo.py examples/intro.paf
    python3 -m unittest discover -s tests
