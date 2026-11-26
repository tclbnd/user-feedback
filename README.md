# mo5scr

Check for the Thomson MO5 screen snapshots (`.mo5`) the art team saves from the
emulator while redrawing French educational and game title screens.
`mo5info.py` rebuilds the 320 × 200 screen and prints one JSON summary per
snapshot.

## File layout

`MO5SCRN` and a NUL, the gate-array register byte at `$A7C0` as last written,
seven padding bytes, then the two 8 KB video planes as the 6809 sees them at
`$0000`–`$1FFF`: first the form (pixel) plane, then the colour plane. Each
screen line is 40 bytes in both planes, line 0 first; the last 192 bytes of
each plane are not displayed.

## Output

| field | meaning |
|---|---|
| `border` | border colour number, one lower-case hex digit |
| `complete` | `true` when both planes are in the file |
| `colors_used` | distinct colour numbers on screen |
| `foreground_pixels` | screen pixels drawn in their cell's foreground colour |
| `first_row`, `row_100`, `last_row` | lines 0, 100 and 199 as runs `[["c", count], ...]`, `c` being the colour number 0–f as one lower-case hex digit |

Screen fields are `null` when the snapshot is incomplete.

## Layout

- `mo5scr/screen.py` – border, planes, colour attributes
- `mo5info.py` – command line

## Running

    python3 mo5info.py examples/title.mo5 examples/quiz.mo5
    python3 -m unittest discover -s tests
