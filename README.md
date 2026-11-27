# anmkit

Frame check for Deluxe Paint Animation (`.anm`) files, the format several
early-90s PC games used for their intro and menu animations. The rip tool
stores the palette and the frame records of one animation as an `.anm` pack;
`anminfo.py` applies every record the way the games' player does and prints
one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `DPANMSEQ` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| 2 | reserved |
| 1024 | palette: 256 entries of four bytes, as stored in the animation's header |
| | for each frame its size (`u32` little-endian) and the frame record as stored in the animation |

All records are applied to one picture in order; the picture starts as all
zero bytes.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per record: `ok` (false when the player rejects the record) and `crc32` of the picture after it as RGB bytes (three per pixel, rows top to bottom, palette colours as the player shows them), lower-case hex |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `anmkit/anm.py` – record decoder
- `anminfo.py` – command line

## Running

    python3 anminfo.py examples/title.anm examples/walk.anm
    python3 -m unittest discover -s tests
