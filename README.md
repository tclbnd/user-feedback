# x68kspr

Check for the X68000 sprite and BG snapshots (`.x68s`) the art team saves from
the emulator while redrawing X68000 game screens. `sceneinfo.py` rebuilds the
256 × 256 sprite/BG screen and prints one JSON summary per snapshot.

## File layout

`X68SPBG` and a NUL, the 128 sprite scroll registers (`$EB0000`, four words
each), then six words of this tool's own layout: BG control, resolution, BG0
X/Y scroll, BG1 X/Y scroll; then the 256 PCG palette words (`$E82200`) and
the 32 KB PCG/BG RAM (`$EB8000`). All words are big-endian as the 68000 sees
them.

BG control: bit 0 BG0 on, bits 2–1 BG0 data area, bit 3 BG1 on, bits 5–4 BG1
data area (0 = `$EBC000`, 1 = `$EBE000`), bit 9 sprite/BG display on.
Resolution: bits 1–0 = 0 for 8 × 8 BG tiles, 1 for 16 × 16 BG tiles. BG maps
are 64 × 64 tiles and wrap; screen pixel `(x, y)` shows map pixel
`(x + X scroll, y + Y scroll)`.

Layer order from back to front: sprites with PRW 1, BG1, sprites with PRW 2,
BG0, sprites with PRW 3. Lower-numbered sprites are in front of higher-numbered
ones. Colour 0 of every layer is transparent; where nothing is drawn the
screen is `#000000`.

## Output

| field | meaning |
|---|---|
| `display` | the display bit as a boolean |
| `bg_tile` | 8 or 16 |
| `complete` | `true` when the whole PCG/BG RAM is in the file |
| `sprites_shown` | sprites that are displayed (0 when display is off) |
| `colors_used` | distinct colours on screen |
| `sprite_pixels` | screen pixels where a sprite is the visible layer |
| `first_row`, `row_128`, `last_row` | lines 0, 128 and 255 as runs `[["#rrggbb", count], ...]` |

Colours follow the X68000 palette as MAME converts it: each component's five
bits followed by the intensity bit form a 6-bit value, widened as
`(c << 2) | (c >> 4)`.

## Layout

- `x68kspr/scene.py` – registers, palette, PCG, BG, layering
- `sceneinfo.py` – command line

## Running

    python3 sceneinfo.py examples/shooter.x68s examples/rpg.x68s
    python3 -m unittest discover -s tests
