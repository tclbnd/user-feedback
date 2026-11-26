# ngspr

Check for the Neo Geo sprite snapshots (`.ngspr`) the art team saves from the
emulator while redrawing character sprites. `sprinfo.py` rebuilds the sprite
layer of the 320 × 224 screen and prints one JSON summary per snapshot.

## File layout

`NGSPR` and three NUL bytes, the video RAM words `$0000`–`$87FF`, the 4096
palette RAM words, then the C ROM tile data up to the end of the file, 128
bytes per 16 × 16 tile with the C1/C2 ROM pair byte-interleaved (C1 at even
offsets) as the cartridge bus reads it. All words are big-endian as the 68000
sees them.

Sprites 1–381 are processed; sprite 0 is not used here. Every sprite in these
snapshots is at full size (SCB2 = `$0FFF`), auto-animation is off and no line
has more than 96 sprites; a shrunk sprite is an error. Pixels where no sprite
draws show the backdrop colour (the last palette RAM word). Tiles past the end
of the C ROM data draw nothing.

## Output

| field | meaning |
|---|---|
| `sprites` | sprites with a non-zero height after resolving chains |
| `tiles` | whole tiles in the C ROM data |
| `missing_tiles` | tile references of those sprites past the end of the C ROM data |
| `colors_used` | distinct colours on screen, backdrop included |
| `sprite_pixels` | screen pixels drawn by a sprite |
| `first_row`, `row_112`, `last_row` | lines 0, 112 and 223 as runs `[["#rrggbb", count], ...]` |

Palette words keep their dark bit uncounted; the 5-bit components are widened
as `(c << 3) | (c >> 2)`.

## Layout

- `ngspr/sprites.py` – SCB1–SCB4, palettes, C ROM tiles, drawing
- `sprinfo.py` – command line

## Running

    python3 sprinfo.py examples/fighter.ngspr examples/stage.ngspr
    python3 -m unittest discover -s tests
