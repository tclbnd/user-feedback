# stic

Check for the Intellivision background snapshots (`.ibt`) the art team saves
from the emulator while redrawing cartridge screens. `sticinfo.py` rebuilds
the 20 × 12-card BACKTAB background (160 × 96 pixels, one pixel per card
pixel, no MOBs, no border or delays) and prints one JSON summary per
snapshot.

## File layout

`INTVBT` and two NUL bytes, the display mode (1 = colour stack, 0 =
foreground/background), the four colour stack registers CSR0–CSR3, the border
colour and two pad bytes, then the 240 BACKTAB words (little-endian, row by
row), the 2 KB GROM and the 512-byte GRAM. The colour stack starts at CSR0 for
the first card of the frame.

## Output

| field | meaning |
|---|---|
| `mode` | `"colour-stack"` or `"foreground-background"` |
| `cards` | object counting cards drawn from `grom`, from `gram`, and as coloured `squares` |
| `colour_pixels` | 16 integers: pixels of each colour number |
| `colors_used` | distinct colours in the image |
| `first_row`, `row_48`, `last_row` | rows 0, 48 and 95 as runs `[["#rrggbb", count], ...]` |

Colours 0–15 are black `#000000`, blue `#002dff`, red `#ff3d10`, tan
`#c9cfab`, dark green `#386b3f`, green `#00a756`, yellow `#faea50`, white
`#fffcff`, grey `#bdacc8`, cyan `#24b8ff`, orange `#ffb41f`, brown `#546e00`,
pink `#ff4e57`, light blue `#a496ff`, yellow green `#75cc80`, purple
`#b51a58`.

## Layout

- `stic/backtab.py` – BACKTAB decoding, colour stack, cards
- `sticinfo.py` – command line

## Running

    python3 sticinfo.py examples/title.ibt examples/field.ibt
    python3 -m unittest discover -s tests
