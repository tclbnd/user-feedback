# spritekit

Check for the RISC OS sprite files (`.spr`) the art team pulls off old Acorn
Archimedes game discs. `sprinfo.py` prints one JSON summary per file with
every sprite's size, colours and transparency, so redrawn sprites can be
compared with the originals.

## Input

Sprite files exactly as RISC OS saves them: the sprite area without its
first word, then the sprites one after another, in old-style screen modes and
new-format sprite types, with or without palettes and masks.

## Output

| field | meaning |
|---|---|
| `sprites_declared` | number of sprites given in the header |
| `complete` | `true` when every sprite and its pixel data are in the file |
| `sprites` | one object per sprite, in file order |

Each sprite object:

| field | meaning |
|---|---|
| `name` | sprite name, trailing padding removed |
| `width`, `height` | displayed size in pixels |
| `bpp` | bits per pixel |
| `has_mask` | `true` when the sprite has a mask |
| `palette_entries` | number of palette entries stored with the sprite (0 without a palette) |
| `first_row` | colours of up to the first 8 pixels of the top row, `"#rrggbb"` lower case; a palette-indexed pixel without a matching palette entry is `null` |
| `last_pixel` | colour of the bottom-right pixel, same notation |
| `transparent_pixels` | pixels the mask hides (0 without a mask) |

`first_row`, `last_pixel` and `transparent_pixels` are `null` when the
sprite's data is not in the file. 16-bit components are widened to 8 bits as
`round(c × 255 / 31)`.

## Layout

- `spritekit/area.py` – area header, sprites, palettes, masks
- `sprinfo.py` – command line

## Running

    python3 sprinfo.py examples/ships.spr examples/icons.spr
    python3 -m unittest discover -s tests
