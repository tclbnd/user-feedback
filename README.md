# orickit

Check for Oric-1 / Atmos HIRES screens dumped from the art team's tape
images (`.hir`). `hirinfo.py` prints one JSON summary per screen so a
converted picture can be compared with how it looks on the machine.

## File layout

A `.hir` file is the raw screen memory from `$A000`: 200 lines of 40 bytes,
top line first. A dump cut short keeps its whole lines only. Bytes are
interpreted the way the Oric video chip shows them in HIRES mode.

## Output

| field | meaning |
|---|---|
| `lines` | number of whole 40-byte lines in the file |
| `complete` | `true` for a full 8000-byte screen |
| `histogram` | 8 integers: how many of the 240 × lines displayed pixels show each colour 0–7 |
| `colors_used` | how many of the 8 colours occur |
| `attribute_bytes` | how many bytes are attributes rather than pixel data |
| `inverse_cells` | how many bytes are displayed inverted |
| `first_line`, `last_line` | the displayed colours of the top and bottom whole line as 240 digits 0–7, left to right |

Colours use the Oric numbering: 0 black, 1 red, 2 green, 3 yellow, 4 blue,
5 magenta, 6 cyan, 7 white.

## Layout

- `orickit/hires.py` – screen decoding
- `hirinfo.py` – command line

## Running

    python3 hirinfo.py examples/title.hir examples/map.hir
    python3 -m unittest discover -s tests
