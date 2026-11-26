# pmprc

Check for the Pokemon mini rendering snapshots (`.pmp`) the art team saves
from the emulator while redrawing handheld screens. `prcinfo.py` runs one PRC
frame (map, then sprites, then the LCD copy) on the snapshot and prints one
JSON summary of the 96 × 64 LCD.

## File layout

`PMPRC` and three NUL bytes, the PRC registers `$2080`–`$208F` as last
written, then the CPU memory image `$00000`–`$1FFFF` (RAM at `$1000`–`$1FFF`,
cartridge data above it). Tile bases always point inside this image.

The PRC here draws the 12 × 8 tiles at the top-left of the map with no
scrolling, and the global invert bit has no effect. An LCD that is not
updated shows light pixels.

## Output

| field | meaning |
|---|---|
| `map_width` | tiles per map row for the selected map size |
| `sprites_shown` | enabled sprites drawn |
| `dark_pixels` | dark LCD pixels |
| `first_row`, `row_32`, `last_row` | lines 0, 32 and 63 as runs `[["#rrggbb", count], ...]`; light is `#fffb87`, dark `#4e4e4e` |

## Layout

- `pmprc/render.py` – registers, map, sprites, frame buffer, LCD
- `prcinfo.py` – command line

## Running

    python3 prcinfo.py examples/title.pmp examples/battle.pmp
    python3 -m unittest discover -s tests
