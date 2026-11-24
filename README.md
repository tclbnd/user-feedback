# koalakit

Check for the Commodore 64 Koala Painter pictures (`.koa`, multicolour
bitmaps) in the retro art archive. `koalainfo.py` prints one JSON summary per
picture, used to verify the colours before the pictures are converted for the
web gallery.

## Output

| field | meaning |
|---|---|
| `load_address` | `$XXXX` when the file starts with a load address (10003-byte files, also with up to 3 bytes of padding), `null` for 10001-byte files |
| `background` | the background colour name |
| `points` | the colour name at `top_left` (0, 0), `top_right` (159, 0), `row_7` (0, 7), `row_8` (0, 8), `center` (80, 100) and `bottom_right` (159, 199), on the 160 × 200 multicolour grid |
| `colors_used` | names of the colours the picture uses, in colour number order |
| `most_common` | the colour covering most pixels (on a tie, the lower colour number) |

Colour names, by C64 colour number 0–15: `black`, `white`, `red`, `cyan`,
`purple`, `green`, `blue`, `yellow`, `orange`, `brown`, `light_red`,
`dark_grey`, `grey`, `light_green`, `light_blue`, `light_grey`.

The file layout (bitmap, screen RAM, colour RAM, background) and how a
multicolour pixel picks its colour follow the Koala Painter format and the
C64 VIC-II multicolour bitmap mode.

## Layout

- `koalakit/koala.py` – layout, pixel colours
- `koalainfo.py` – command line

## Running

    python3 koalainfo.py examples/sunset.koa examples/logo.koa
    python3 -m unittest discover -s tests
