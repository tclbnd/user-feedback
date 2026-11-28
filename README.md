# pictkit

Picture check for Macintosh QuickDraw pictures (PICT, version 2), as found in
old Mac game resources, clip-art collections and screenshots. `pictinfo.py`
decodes the first pixel image in each picture the way QuickDraw draws it and
prints one JSON summary per file.

## File layout

A version 2 picture starts with the picture size and frame, then the version
opcode `0011 02FF` and the header opcode `0C00`, followed by word-aligned
opcodes up to `00FF`. Pixel data comes in `PackBitsRect` / `PackBitsRgn`
(bitmaps and indexed pixel maps with a colour table) and `DirectBitsRect` /
`DirectBitsRgn` (16-bit and 32-bit direct pixel maps). Each row of pixel
data is stored with PackBits, preceded by its packed byte count.

## Output

| field | meaning |
|---|---|
| `width`, `height` | size of the pixel image (its bounds rectangle) |
| `kind` | `bitmap`, `indexed` or `direct` |
| `depth` | bits per pixel |
| `crc32` | CRC-32 (lower-case hex) of the image as RGB, three bytes per pixel, rows top to bottom, colours as QuickDraw shows them (16-bit components keep their high byte, 5-bit components are widened to 8 bits by repeating their top bits) |
| `colours` | number of distinct RGB colours in the image |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `pictkit/pict.py` – opcode walker, colour table and pixel decoder
- `pictinfo.py` – command line

## Running

    python3 pictinfo.py examples/splash.pict examples/photo.pict examples/icons.pict
    python3 -m unittest discover -s tests
