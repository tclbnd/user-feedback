# gime

Check for the CoCo 3 graphics snapshots (`.gime`) the art team saves from the
emulator while redrawing CoCo 3 title screens. `gimeinfo.py` rebuilds the
graphics screen the GIME displays and prints one JSON summary per snapshot.

## File layout

`GIME3SN` and a NUL, the eight video registers `$FF98`–`$FF9F` as last
written, the 16 palette registers `$FFB0`–`$FFBF` (RGB monitor), then the
512 KB physical memory image. Only graphics modes occur; text mode is an
error, as are resolution codes the GIME leaves undefined. With the horizontal
virtual enable bit set, horizontal offsets wrap inside the 256-byte row.

## Output

| field | meaning |
|---|---|
| `width`, `height` | screen size in pixels |
| `colors` | 2, 4 or 16 |
| `start` | physical address of the first displayed byte |
| `complete` | `true` when the whole memory image is in the file |
| `border` | border colour, `"#rrggbb"` |
| `palette` | the 16 palette colours, `"#rrggbb"` |
| `colors_used` | distinct colours on screen |
| `first_row`, `last_row` | top and bottom screen lines as runs `[["#rrggbb", count], ...]` |

2-bit components are widened as `c × 85`. Screen fields are `null` when the
memory image is incomplete.

## Layout

- `gime/screen.py` – registers, palette, memory, drawing
- `gimeinfo.py` – command line

## Running

    python3 gimeinfo.py examples/title.gime examples/game.gime
    python3 -m unittest discover -s tests
