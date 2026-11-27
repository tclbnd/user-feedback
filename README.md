# pvgfx

Check for the Casio PV-1000 screen snapshots (`.pv`) the art team saves from
the emulator while redrawing cartridge title screens. `pvinfo.py` rebuilds the
256 × 192 picture the D65010 video chip shows and prints one JSON summary per
snapshot.

## File layout

`PV1000VR`, I/O register 7 as last written, the 1 KB video RAM (a 32 × 24 tile
map, row by row), the 1 KB pattern RAM, then the pattern ROM from the
cartridge (32 bytes per pattern, pattern 0 first).

## Display rules (D65010)

- Register 7: bits 7–5 select the ROM pattern bank (256 patterns per bank),
  bit 3 switches the tile layer off (the whole screen becomes the border
  colour), bits 2–0 are the border colour.
- The tile map is 32 × 24 tiles of 8 × 8 pixels, but the leftmost and
  rightmost tile columns belong to the border, not the tile layer.
- Tiles `0x00`–`0xdf` come from the selected ROM pattern bank; tiles
  `0xe0`–`0xff` come from the 32 patterns in pattern RAM instead.
- Each 32-byte pattern holds 8 unused bytes, then three 8-byte planes
  (one bit per pixel each, bit 7 leftmost). The first plane is the high
  colour bit, the last plane the low one.
- Colour values map straight to the chip's three digital guns: bit 0 is red,
  bit 1 green, bit 2 blue.

## Output

| field | meaning |
|---|---|
| `border` | border colour |
| `display` | `false` when the tile layer is switched off |
| `pattern_bank` | ROM pattern bank selected by register 7 |
| `ram_cells` | screen cells drawn from the pattern RAM |
| `missing_patterns` | screen cells whose ROM pattern is not in the file (drawn as colour 0) |
| `colors_used` | distinct colours in the picture |
| `first_row`, `row_96`, `last_row` | pixel lines 0, 96 and 191 (256 pixels each, including the side columns) as runs `[["#rrggbb", count], ...]` |

Colours are the chip's eight digital colours, each gun fully on or off.

## Layout

- `pvgfx/screen.py` – register 7, tile map, patterns
- `pvinfo.py` – command line

## Running

    python3 pvinfo.py examples/title.pv examples/maze.pv
    python3 -m unittest discover -s tests
