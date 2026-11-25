# vdckit

Check for Commodore 128 80-column screens saved by the art team's emulator
as VDC snapshots (`.vdc`). `vdcinfo.py` prints one JSON summary per snapshot
with colour counts and the top pixel line, so a redrawn business-program
screen can be compared with what the RGBI monitor showed.

## File layout

`VDCSNAP`, a zero byte, the 37 VDC registers R0–R36 as they stood at capture
time, the screen (one character code per cell, row by row), the attributes
(one byte per cell), then the character set memory: 512 characters of 16
bytes each, top row first. The screen size and character height follow from
the registers, and everything is displayed the way the VDC displays it.

## Output

| field | meaning |
|---|---|
| `columns`, `rows` | text columns and rows |
| `char_height` | scan lines per character row |
| `width`, `height` | displayed size in pixels (`columns × 8`, `rows × char_height`) |
| `histogram` | 16 integers: displayed pixels per RGBI colour 0–15 |
| `colors_used` | how many colours occur |
| `first_line` | the top pixel line, one hex digit per pixel |
| `reverse_cells`, `underline_cells`, `blink_cells`, `alternate_cells` | cells whose attribute asks for each effect (0 when attributes are off) |

Blinking is counted only; pixels are taken in the blink "on" phase.

## Layout

- `vdckit/screen.py` – snapshot decoding
- `vdcinfo.py` – command line

## Running

    python3 vdcinfo.py examples/menu.vdc examples/sheet.vdc
    python3 -m unittest discover -s tests
