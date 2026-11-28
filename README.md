# rlekit

Frame check for the Microsoft RLE videos that many mid-90s Windows games ship
as AVI intros and in-game clips. The rip tool stores the palette and the RLE
frames of one clip as an `.rle` pack; `rleinfo.py` decodes every frame the
way the Windows codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `MSRLEPAK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 1 + 1 | bits per pixel (8 for RLE8 or 4 for RLE4), reserved |
| 2 | frame count (`u16` little-endian) |
| 1024 | palette, 256 entries of four bytes as in the AVI's bitmap header |
| | for each frame its size (`u32` little-endian) and the frame as stored in the AVI |

Frames are standard Microsoft RLE8 or RLE4 bitmap data. All frames are drawn
onto one picture that starts as all zero bytes, so a frame only changes the
pixels it codes.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `depth` | header values |
| `frames` | one object per frame: `state` (`decoded` when the frame ended with its end code, `no end code` when the data ran out first, `error` when the codec stopped on bad data) and `crc32` of the picture after the frame as RGB bytes (three per pixel, rows top to bottom), lower-case hex |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `rlekit/msrle.py` – RLE8 and RLE4 decoders
- `rleinfo.py` – command line

## Running

    python3 rleinfo.py examples/intro.rle examples/map.rle
    python3 -m unittest discover -s tests
