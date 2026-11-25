# bmpkit

Intake check for the bitmap artwork, maps and UI captures in the game and
broadcast graphics archive (`.bmp`). `bmpinfo.py` prints one JSON summary per
file, used to check dimensions, pixel format and colour data.

## Output

| field | meaning |
|---|---|
| `header` | the DIB header: `core` (OS/2 1.x, 12 bytes), `info` (40), `v2` (52), `v3` (56), `os2v2` (64), `v4` (108), `v5` (124) |
| `width`, `height` | in pixels (positive integers) |
| `top_down` | `true` when the rows are stored top row first |
| `bit_count` | bits per pixel (an integer) |
| `compression` | `rgb`, `rle8`, `rle4`, `bitfields`, `jpeg`, `png`, `alphabitfields`, `huffman1d`, `rle24`, else `type_N` |
| `palette_colors` | the number of colour table entries (an integer, 0 above 8 bits per pixel) |
| `row_bytes` | bytes per stored row for uncompressed and bit-field data, else `null` |
| `image_bytes` | `row_bytes × height` for uncompressed and bit-field data, else the size of the compressed data |
| `masks` | the red, green, blue and alpha masks in use for 16- and 32-bit pixels as 8 lower-case hex digits each (`00000000` for none), else `null` |
| `alpha` | `true` when the alpha mask is not 0 |
| `dpi` | `[x, y]` resolution in dots per inch, rounded (integers) |
| `color_space` | for V4/V5 headers: `sRGB`, `windows`, `linked`, `embedded`, `calibrated`, else `type_xxxxxxxx`; else `null` |
| `profile_bytes` | the size of a linked or embedded ICC profile (V5), else `null` |
| `corner_pixel` | the colour shown at the picture's top-left corner as `#rrggbb` (uncompressed and bit-field data only, else `null`), mask fields scaled to 8 bits with rounding |

Header layouts, palettes, default pixel formats, bit-field masks and row
storage follow the Windows bitmap formats (BITMAPCOREHEADER through
BITMAPV5HEADER).

## Layout

- `bmpkit/bmp.py` – file and DIB headers, palette, masks, rows
- `bmpinfo.py` – command line

## Running

    python3 bmpinfo.py examples/map.bmp examples/ui_capture.bmp
    python3 -m unittest discover -s tests
