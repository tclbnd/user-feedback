# cyuvkit

Frame check for AVI clips coded with Creative Labs' YUV codec (`CYUV`) and
the closely related Auravision Aura codec (`AURA`), common on mid-90s
capture-card and multimedia CD-ROM titles. The rip tool stores the frames of
one clip as a pack; `cyuvinfo.py` decodes every frame the way the codec does
and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `CYUVPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 4 | codec FourCC, `CYUV` or `AURA` |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame exactly as stored in the AVI |

A coded frame starts with three 16-entry tables of prediction differences,
followed by three bytes for every four pixels of each row (4-bit codes for
Y, U and V, 4:1:1 subsampling). Some frames are stored uncompressed as UYVY
instead; the frame size tells the two apart.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `codec` | header values |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the codec rejects the frame); decoded frames also give `format` (`yuv411p` or `uyvy`) and `crc32` (lower-case hex) of the picture as the codec outputs it: for `yuv411p` the Y plane then U then V (quarter width), for `uyvy` the packed rows, rows top to bottom |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `cyuvkit/cyuv.py` – pack reader and frame decoder
- `cyuvinfo.py` – command line

## Running

    python3 cyuvinfo.py examples/clip.cyuv examples/logo.aura
    python3 -m unittest discover -s tests
