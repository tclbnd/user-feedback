# ndstex

Check for the NDS 3D texture captures (`.ntx`) the art team takes from the
emulator while repainting models. `texinfo.py` prints one JSON summary per
capture with the texel colours as the NDS 3D engine samples them.

## File layout

`NDSTEX` and two NUL bytes, then little-endian fields: the `TEXIMAGE_PARAM`
value (`u32`), the `PLTT_BASE` value (`u16`), two pad bytes, and the byte
lengths of the three blocks that follow (`u32` each): texture data (from the
texture's start address), texture palette RAM (from palette address 0) and,
for 4×4 compressed textures, the palette index data belonging to the texture
(one 16-bit entry per block, in block order). Palette colours past the end of
the palette block read as black.

## Output

| field | meaning |
|---|---|
| `format` | `"A3I5"`, `"4-colour"`, `"16-colour"`, `"256-colour"`, `"4x4"`, `"A5I3"` or `"direct"` |
| `width`, `height` | texture size |
| `colour0_transparent` | the colour 0 transparent flag as a boolean |
| `complete` | `true` when all data the texture needs is in the file |
| `colors_used` | distinct texel values (colour and alpha) |
| `transparent_pixels` | texels with alpha 0 |
| `first_row`, `last_row` | top and bottom texel rows as runs `[["#rrggbbaa", count], ...]` |

Colour and alpha are 5-bit values widened as `(v << 3) | (v >> 2)`. A
transparent texel is written `#00000000`. Texel fields are `null` when data is
missing.

## Layout

- `ndstex/texture.py` – header, palettes, texel decoding
- `texinfo.py` – command line

## Running

    python3 texinfo.py examples/face.ntx examples/glass.ntx
    python3 -m unittest discover -s tests
