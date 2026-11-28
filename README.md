# dfakit

Frame check for the DFA cut-scenes of Chronomaster. The rip tool stores the
frames of one movie as a `.dfa` pack; `dfainfo.py` decodes every frame the
way the game's player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `DFAMOVIE` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | version from the movie header (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame as stored in the movie |

A frame is a list of chunks, each with a 4-byte name, a `u32` size and a
`u32` type, ending at a chunk of type 0. Type 1 is a palette of 6-bit red,
green, blue components; types 2 to 9 are COPY, TSW1, BDLT, WDLT, TDLT, DSW1,
BLCK and DDS1 picture chunks. All frames are drawn onto one picture that
starts as all zero bytes; the palette starts black. Movies with version 0x100
store the picture in four interleaved parts that the player puts back
together for display.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `version` | header values |
| `frames` | one object per frame: `ok`, and `crc32` of the displayed picture as RGB bytes (three per pixel, rows top to bottom, palette colours as the player shows them), lower-case hex; a frame the player rejects gives `ok` false and `crc32` null |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `dfakit/dfa.py` – chunk decoders
- `dfainfo.py` – command line

## Running

    python3 dfainfo.py examples/intro.dfa examples/lab.dfa
    python3 -m unittest discover -s tests
