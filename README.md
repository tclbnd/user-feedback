# vbvip

Check for the Virtual Boy world snapshots (`.vbw`) the art team saves from the
emulator while redrawing stages. `vbinfo.py` prints one JSON summary per
snapshot with the red LED levels of the 384 × 224 screen as the VIP draws one
normal BG world.

## File layout

`VBVIP` and a NUL; the BRTA, BRTB, BRTC and BKCOL registers (one byte each);
GPLT0–GPLT3 (one byte each); the world's GX, GY, MX, MY (signed 16-bit) and
W, H (16-bit) exactly as in the world attribute table, all little-endian; the
2048 characters (16 bytes each, character 0 first); the BG map (64 × 64 cells,
2 bytes each, row by row).

## Output

| field | meaning |
|---|---|
| `window` | `[x, y, width, height]` of the world on screen, in pixels |
| `levels` | 4 integers: LED level of shade 0, 1, 2 and 3 |
| `background` | LED level shown where nothing is drawn |
| `complete` | `true` when the whole BG map is in the file |
| `screen` | LED level (as a string) → number of screen pixels |
| `first_line`, `last_line` | top and bottom lines of the world window as runs `[[level, count], ...]` |

Screen fields are `null` when the snapshot is incomplete.

## Layout

- `vbvip/world.py` – registers, characters, BG map, drawing
- `vbinfo.py` – command line

## Running

    python3 vbinfo.py examples/stage.vbw examples/title.vbw
    python3 -m unittest discover -s tests
