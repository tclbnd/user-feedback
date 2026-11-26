# timkit

Check for the TIM textures (`.tim`) the art team extracts from PlayStation
discs. `timinfo.py` prints one JSON summary per texture, so a converted
texture can be compared with how the console draws it.

## Input

TIM files exactly as the games store them: the TIM header, an optional CLUT
block and the image block, in 4-, 8-, 16- and 24-bit modes.

## Output

| field | meaning |
|---|---|
| `bpp` | 4, 8, 16 or 24 |
| `clut` | `{"x", "y", "colors", "palettes"}` of the CLUT block (frame buffer position, colours per palette, number of palettes), `null` without one |
| `x`, `y` | frame buffer position of the image |
| `width`, `height` | image size in pixels |
| `rows`, `complete` | whole pixel rows present; `true` when all `height` rows are |
| `colors_used` | number of distinct displayed colours (pixels without a colour not counted) |
| `first_row` | up to the first 8 displayed colours of the top row, `"#rrggbb"` lower case, `null` for a pixel without a colour |
| `last_pixel` | displayed colour of the last pixel of the last whole row |
| `transparent_pixels` | pixels the GPU never draws |
| `semi_transparent_pixels` | other pixels drawn semi-transparently when the primitive has semi-transparency on |

4- and 8-bit pixels take their colour from the first palette of the CLUT;
without a CLUT they have no colour. 5-bit components are widened to 8 bits as
`round(c × 255 / 31)`. `transparent_pixels` and `semi_transparent_pixels`
count 4-, 8- and 16-bit pixels only.

## Layout

- `timkit/tim.py` – header, CLUT, image, texels
- `timinfo.py` – command line

## Running

    python3 timinfo.py examples/face.tim examples/menu.tim
    python3 -m unittest discover -s tests
