# vickit

Check for VIC-20 screens saved by the art team's emulator as snapshots
(`.v20`). `vicinfo.py` prints one JSON summary per snapshot so a redrawn
title screen can be compared with what the VIC chip puts on the television.

## File layout

`VIC20SNP`, the 16 VIC registers `$9000`–`$900F` as they were at capture
time, the screen matrix (one character code per cell, row by row), the colour
RAM (one byte per cell, low nibble significant), then the character
generator: 256 characters of 8 or 16 bytes each, top row first. How many
cells there are and how tall a character is follow from the registers.

## Output

| field | meaning |
|---|---|
| `columns`, `rows` | text columns and rows on screen |
| `char_height` | 8 or 16 |
| `width`, `height` | displayed size in pixels (`columns × 8`, `rows × char_height`) |
| `multicolor_cells` | cells shown in multicolour mode |
| `histogram` | 16 integers: displayed pixels per VIC colour 0–15 (inside the text area, border not included) |
| `colors_used` | how many colours occur in the text area |
| `first_line`, `last_line` | top and bottom pixel line of the text area as one hex digit per pixel; a multicolour pixel counts as two pixels of its colour |

## Layout

- `vickit/screen.py` – snapshot decoding
- `vicinfo.py` – command line

## Running

    python3 vicinfo.py examples/title.v20 examples/game.v20
    python3 -m unittest discover -s tests
