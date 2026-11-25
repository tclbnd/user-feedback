# tilekit

Check for the tile sets the art team exports from console ROMs (`.tiles`).
`tileinfo.py` prints one JSON summary per tile set, used to spot-check an
export before the tiles go to the remastering artists.

## File layout

A `.tiles` file is the export tool's container: `TILE`, a 4-character
platform tag (`NES `, `GB  `, `SNES`, `MD  `, `GBA `), the tile count and the
number of palette entries (both 16-bit little-endian), the palette exactly as
the console stores it (none for NES, the one-byte BGP register for GB, one
16-bit colour word per entry for the others), then the tiles exactly as they
sit in the console's video memory (2 bits per pixel for NES and GB, 4 bits
per pixel for SNES, MD and GBA; 8 × 8 pixels per tile).

## Output

| field | meaning |
|---|---|
| `platform`, `bpp` | platform tag (spaces removed) and bits per pixel |
| `tiles` | tile count from the header |
| `complete` | `true` when the file holds every tile |
| `palette` | the palette as `#rrggbb` strings in entry order (`null` for NES); GB shades are `#ffffff`, `#aaaaaa`, `#555555`, `#000000` |
| `histogram` | how many pixels use each colour index (4 or 16 integers) |
| `first_tile` | the first tile as 8 strings of 8 hex digits, top row first, leftmost pixel first |
| `last_row` | the bottom row of the last tile, same notation |
| `colors_used` | how many colour indices occur |

Colour components are widened to 8 bits as `round(c × 255 / max)`. Tile
data and palettes follow each console's own video hardware.

## Layout

- `tilekit/tiles.py` – container, palettes, tiles
- `tileinfo.py` – command line

## Running

    python3 tileinfo.py examples/hero.tiles examples/font.tiles
    python3 -m unittest discover -s tests
