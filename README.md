# mzvid

Check for the Sharp MZ-700 screen snapshots (`.mzv`) the art team saves from the
emulator while redrawing title and menu screens. `mzinfo.py` rebuilds the
320 × 200 character screen and prints one JSON summary per snapshot.

## File layout

`MZ700VR` and a NUL, then the 1000 bytes of character VRAM (`$D000`–`$D3E7`,
40 columns × 25 rows, row by row), the 1000 bytes of colour VRAM
(`$D800`–`$DBE7`, one byte per character cell) and the 4 KB character
generator ROM (512 characters, 8 bytes each, top row first).

## Output

| field | meaning |
|---|---|
| `width`, `height` | 320 and 200 |
| `complete` | `true` when the colour VRAM and the whole character generator are in the file |
| `alt_cells` | cells drawn from the second character set (characters 256–511) |
| `colors_used` | distinct colours on screen |
| `first_row`, `row_100`, `last_row` | pixel lines 0, 100 and 199 as runs `[["#rrggbb", count], ...]` |

Colours are the eight digital colours of the MZ-700 colour monitor output, each
gun fully on (`ff`) or off (`00`). Screen fields are `null` when the snapshot is
incomplete.

## Layout

- `mzvid/screen.py` – VRAM, colour attributes, character generator
- `mzinfo.py` – command line

## Running

    python3 mzinfo.py examples/title.mzv examples/menu.mzv
    python3 -m unittest discover -s tests
