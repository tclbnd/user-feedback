# zx81kit

Check for ZX81 screens saved from the art team's emulator as display file
dumps (`.dfl`). `dfinfo.py` prints one JSON summary per dump with the screen
text and pixel counts, so a recreated title page can be checked against the
original program.

## File layout

`ZX81DF`, the 64-character set the screen was drawn with (8 bytes per
character, top row first, set bits are black), then the display file
exactly as it sat in memory from `D_FILE`. The display file is laid out the
way the ZX81 itself keeps and displays it.

## Output

| field | meaning |
|---|---|
| `rows` | text rows found in the display file (at most 24) |
| `collapsed` | `true` when any row holds fewer than 32 characters |
| `text` | 24 strings, one per screen row, trailing spaces removed; characters in the ZX81 character set order, every block graphic shown as `#`, an inverse character shown as its normal form |
| `inverse_chars` | characters displayed inverted on the 32 × 24 screen |
| `ink_pixels` | black pixels on the 256 × 192 screen |
| `first_line` | the top pixel line as 256 digits, `1` black, left to right |

## Layout

- `zx81kit/dfile.py` – display file decoding
- `dfinfo.py` – command line

## Running

    python3 dfinfo.py examples/title.dfl examples/menu.dfl
    python3 -m unittest discover -s tests
