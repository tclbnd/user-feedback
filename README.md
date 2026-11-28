# mwsckit

Frame check for MatchWare screen recordings (the `MWSC` codec of MatchWare's
screen-capture and e-learning tools). The rip tool stores the frames of one
recording as an `.mwsc` pack; `mwscinfo.py` decodes every frame the way the
MWSC codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `MWSCPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame exactly as stored in the AVI |

Each frame is one zlib stream of run records: a 24-bit pixel (B, G, R) and a
run byte. Most runs repeat the pixel; special run values extend the count
or copy pixels unchanged from the previous frame. Pixels are written from
the bottom row of the picture upwards.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the codec rejects the frame); decoded frames also give `key` (whether the frame stands on its own, without copies from the previous frame) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `mwsckit/mwsc.py` – pack reader and run decoder
- `mwscinfo.py` – command line

## Running

    python3 mwscinfo.py examples/lesson.mwsc
    python3 -m unittest discover -s tests
