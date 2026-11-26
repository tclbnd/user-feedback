# pcevdc

Check for the PC Engine video snapshots (`.pcev`) the art team saves from the
emulator while redrawing stages. `vdcinfo.py` rebuilds the 256 × 224 screen
from the HuC6270 VDC and HuC6260 VCE state and prints one JSON summary per
snapshot.

## File layout

`PCEVDC` and two NUL bytes, the VDC registers MWR, BXR, BYR and CR, then the
32K words of VRAM, the 64-entry sprite attribute table as the SATB DMA copied
it (four words per sprite), and the 512-entry VCE colour table. All words are
little-endian.

Screen line `n` shows background line `BYR + n` and screen column `n`
background column `BXR + n`. CR bit 7 turns the background on and bit 6 the
sprites. Lower-numbered sprites are in front of higher-numbered ones, and no
line in these snapshots has more than 16 sprite cells.

## Output

| field | meaning |
|---|---|
| `bat_width`, `bat_height` | virtual screen size in tiles |
| `background`, `sprites_on` | CR bits 7 and 6 |
| `sprites_shown` | sprites with at least one cell on screen (0 when sprites are off) |
| `colors_used` | distinct colours on screen |
| `sprite_pixels` | screen pixels where a sprite pixel is shown |
| `first_row`, `row_112`, `last_row` | lines 0, 112 and 223 as runs `[["#rrggbb", count], ...]` |

3-bit components are widened as `(c << 5) | (c << 2) | (c >> 1)`. A snapshot
shorter than the full layout is an error.

## Layout

- `pcevdc/screen.py` – registers, BAT, tiles, sprites, colour table
- `vdcinfo.py` – command line

## Running

    python3 vdcinfo.py examples/stage.pcev examples/boss.pcev
    python3 -m unittest discover -s tests
