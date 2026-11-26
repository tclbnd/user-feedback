# lynxspr

Check for the Atari Lynx sprite snapshots (`.lnxs`) the art team saves from
the emulator while redrawing handheld sprites. `suzyinfo.py` runs the Suzy SCB
chain into a fresh 160 × 102 frame and prints one JSON summary per snapshot.

## File layout

`LYNXSCB` and a NUL, the address of the first SCB (little-endian word), the 16
`GREEN` registers (`$FDA0`–`$FDAF`), the 16 `BLUERED` registers
(`$FDB0`–`$FDBF`), then the 64 KB RAM image.

The frame starts filled with pen 0, and the pen index table starts as the
identity mapping. In these snapshots every sprite is at 1:1 size (no stretch
or tilt), starts in the down/right quadrant, never switches quadrant (a line
offset of 1 simply ends the sprite here), and HOFF/VOFF are 0. A scaled sprite
or another draw origin is an error.

## Output

| field | meaning |
|---|---|
| `sprites_drawn`, `sprites_skipped` | SCBs drawn and SCBs skipped |
| `pen_pixels` | 16 integers: frame pixels left at each pen |
| `colors_used` | distinct colours in the frame |
| `first_row`, `row_51`, `last_row` | lines 0, 51 and 101 as runs `[["#rrggbb", count], ...]` |

4-bit components are widened as `v × 17`.

## Layout

- `lynxspr/suzy.py` – SCBs, sprite lines, palette
- `suzyinfo.py` – command line

## Running

    python3 suzyinfo.py examples/title.lnxs examples/level.lnxs
    python3 -m unittest discover -s tests
