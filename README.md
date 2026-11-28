# pixkit

Check for BRender pixel maps (`.pix`), the texture and image files used by
games built on Argonaut's BRender 3D engine. `pixinfo.py` decodes each file
the way BRender loads it and prints one JSON summary per file.

## File layout

A pixel map starts with the four 32-bit big-endian words `0x12 0x08 0x02
0x02`, then a sequence of chunks, each a 32-bit chunk type followed by its
data: a pixel map header (format code, width, height and the map's name), an
optional palette (a header of its own and a chunk holding 256 entries) and
the image data chunk. Format codes: 3 indexed, 4 RGB 555, 5 RGB 565, 6 RGB
24, 7 xRGB 32, 8 ARGB 32, 18 grey with alpha. All values are big-endian.
Indexed maps without a palette chunk use BRender's standard palette
(`STD_PAL` in `pixkit/pix.py`).

## Output

| field | meaning |
|---|---|
| `format` | `pal8`, `rgb555`, `rgb565`, `rgb24`, `xrgb`, `argb` or `ya8` |
| `width`, `height` | picture size |
| `crc32` | CRC-32 (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom; 5- and 6-bit components widened to 8 bits by repeating their top bits, grey repeated into R, G and B |
| `alpha_crc32` | CRC-32 of the alpha values (one byte per pixel) for formats with alpha, otherwise `null` |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `pixkit/pix.py` – chunk reader and pixel conversion
- `pixinfo.py` – command line

## Running

    python3 pixinfo.py examples/brick.pix examples/sky.pix examples/sign.pix
    python3 -m unittest discover -s tests
