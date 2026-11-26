# gbcbg

Check for the Game Boy Color background snapshots (`.gbcbg`) the art team
saves from the emulator while redrawing stages. `bginfo.py` prints one JSON
summary per snapshot with the colours of the 160 × 144 screen as the CGB
draws the background layer.

## File layout

`GBCBG` and a NUL, the LCDC, SCX and SCY registers and a pad byte, the 64 bytes
of background palette RAM (8 palettes of 4 colours), then VRAM bank 0 and VRAM
bank 1 (8 KB each, `$8000`–`$9FFF`). Only the background layer is drawn;
window and sprites are not.

## Output

| field | meaning |
|---|---|
| `lcdc` | the LCDC value |
| `complete` | `true` when both VRAM banks are in the file |
| `palettes` | the 32 palette colours, `"#rrggbb"` lower case, palette 0 colour 0 first |
| `colors_used` | distinct colours on screen |
| `palette_pixels` | 8 integers: screen pixels drawn with each background palette |
| `first_line`, `last_line` | top and bottom screen lines as runs `[["#rrggbb", count], ...]` |

5-bit components are widened as `round(c × 255 / 31)`. Screen fields are
`null` when the snapshot is incomplete.

## Layout

- `gbcbg/screen.py` – registers, palettes, VRAM, drawing
- `bginfo.py` – command line

## Running

    python3 bginfo.py examples/stage1.gbcbg examples/title.gbcbg
    python3 -m unittest discover -s tests
