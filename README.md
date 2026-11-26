# tedgfx

Check for the Commodore Plus/4 and C16 bitmap snapshots (`.ted`) the art team
saves from the emulator while redrawing loading and title pictures.
`tedinfo.py` rebuilds the 320 × 200 bitmap screen and prints one JSON summary
per snapshot.

## File layout

`TEDSNAP` and a NUL, the 32 TED registers `$FF00`–`$FF1F` as last written, then
the 64 KB of RAM the TED fetches from (`$0000`–`$FFFF`). Only the two bitmap
modes are covered; a snapshot in a character mode is an error.

## Output

| field | meaning |
|---|---|
| `mode` | `"hires"` or `"multicolour"` |
| `complete` | `true` when the whole 64 KB RAM is in the file |
| `colors_used` | distinct colours on screen |
| `first_row`, `row_100`, `last_row` | pixel lines 0, 100 and 199 (320 pixels each) as runs `[["hh", count], ...]` |

Colours are TED colour numbers written as two lower-case hex digits (luminance
in the high digit, hue in the low digit). Screen fields are `null` when the
snapshot is incomplete.

## Layout

- `tedgfx/bitmap.py` – registers, colour matrices, bitmap
- `tedinfo.py` – command line

## Running

    python3 tedinfo.py examples/title.ted examples/loader.ted
    python3 -m unittest discover -s tests
