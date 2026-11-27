# bfikit

Frame check for the BFI cut-scenes of Interplay's mid-90s CD-ROM games. The
rip tool stores one movie's video track as a `.bfi` pack; `bfiinfo.py`
decodes every frame the way the games' player does and prints one JSON
summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `BFIMOVIE` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | number of palette entries (`u16` little-endian, at most 256) |
| 2 | frame count (`u16` little-endian) |
| 3 × entries | palette, red, green, blue per entry as stored in the movie header |
| | for each frame its size (`u32` little-endian) and the frame data as stored in the movie |

Each frame is one BFI video chunk: the unpacked size (`u32`) followed by the
chain-coded picture. All frames are drawn onto one picture in order; the
picture starts as all zero bytes.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `colours` | header values |
| `frames` | one object per frame: `state` (`decoded` when the frame filled the picture or reached its end code, `stopped` when a chain would run past the picture and the rest of the frame was dropped, `error` when the frame data was damaged) and `crc32` of the picture after that frame as RGB bytes (three per pixel, rows top to bottom, palette colours as the player shows them), lower-case hex |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `bfikit/bfi.py` – palette and frame decoder
- `bfiinfo.py` – command line

## Running

    python3 bfiinfo.py examples/intro.bfi examples/death.bfi
    python3 -m unittest discover -s tests
