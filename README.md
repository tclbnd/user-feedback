# snesbg

Check for the Super NES background snapshots (`.sbg`) the art team saves from
the emulator while redrawing stages. `bginfo.py` prints one JSON summary per
snapshot with the colours of BG1 on the 256 × 224 screen as the PPU draws it.

## File layout

`SNESBG` and two NUL bytes, then the registers `BGMODE` (`$2105`), `BG1SC`
(`$2107`), `BG12NBA` (`$210B`), a pad byte, `BG1HOFS` and `BG1VOFS` (16-bit
little-endian, as last written), then the 512 bytes of CGRAM and the 64 KB of
VRAM, both little-endian words as the PPU holds them.

Only BG1 is drawn, with colour 0 showing the backdrop (CGRAM colour 0). Screen
line `n` shows BG line `BG1VOFS + n` and screen column `n` BG column
`BG1HOFS + n`. Only BG modes 0, 1 and 3 occur in these snapshots (direct
colour off); any other mode is an error.

## Output

| field | meaning |
|---|---|
| `mode` | BG mode number |
| `bpp` | bits per pixel of BG1 in that mode |
| `tile_size` | 8 or 16 |
| `complete` | `true` when the whole VRAM is in the file |
| `colors_used` | distinct colours on screen, backdrop included |
| `transparent_pixels` | screen pixels where the backdrop shows |
| `palette_pixels` | 8 integers: opaque screen pixels drawn with each tilemap palette number |
| `first_row`, `last_row` | top and bottom screen lines as runs `[["#rrggbb", count], ...]` |

5-bit components are widened as `round(c × 255 / 31)`. Screen fields are
`null` when the snapshot is incomplete.

## Layout

- `snesbg/layer.py` – registers, CGRAM, VRAM, drawing
- `bginfo.py` – command line

## Running

    python3 bginfo.py examples/forest.sbg examples/castle.sbg
    python3 -m unittest discover -s tests
