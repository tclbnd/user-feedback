# samkit

Check for SAM Coupé screens dumped from the art team's emulator (`.sam`).
`saminfo.py` prints one JSON summary per dump with the palette, colour counts
and the top line, so a converted picture can be compared with the SAM's own
display.

## File layout

`SAMSCR`, the screen mode byte (`2` or `4`), the 16 palette (CLUT) entries
exactly as written to the SAM's palette registers, then the screen memory
page from its first byte: 24 576 bytes for mode 4, and for mode 2 the
6 144-byte bitmap, the unused bytes up to offset `0x2000` and the 6 144-byte
attribute area. Screens are 256 × 192 pixels and are shown the way the SAM
video hardware shows them.

## Output

| field | meaning |
|---|---|
| `mode` | 2 or 4 |
| `palette` | the 16 CLUT entries as `#rrggbb` |
| `colors` | `{"#rrggbb": n, ...}`: displayed pixels per colour, keys ascending |
| `flash_cells` | mode 2 attribute cells with the flash bit (0 for mode 4) |
| `first_line` | the top line as runs `[["#rrggbb", n], ...]`, left to right |

Colour components are widened to 8 bits as `round(level × 255 / 7)` from
the SAM's 3-bit level per component.

## Layout

- `samkit/screen.py` – screen and palette decoding
- `saminfo.py` – command line

## Running

    python3 saminfo.py examples/title.sam examples/level.sam
    python3 -m unittest discover -s tests
