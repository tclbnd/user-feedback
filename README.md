# neokit

Check for the Atari ST NEOchrome pictures (`.neo`) in the retro art archive.
`neoinfo.py` prints one JSON summary per picture, used to verify palettes,
colour-cycling settings and pixels before the pictures are converted.

## Output

| field | meaning |
|---|---|
| `resolution`, `width`, `height` | `low` (320 × 200, 16 colours), `medium` (640 × 200, 4) or `high` (640 × 400, 2) |
| `filename` | the name stored in the header, trimmed, `null` when empty |
| `palette` | the colours the resolution uses, as `#rrggbb` |
| `cycling` | when colour cycling is on: `left`, `right` (colour numbers), `direction` (`left` or `right`), `delay` (in vertical blanks) and `steps`; else `null` |
| `offset` | `[x, y]` image offset from the header |
| `points` | at `top_left` (0, 0), `x15` (15, 0), `x16` (16, 0), `center` (160, 100) and `bottom_right` (width − 1, height − 1): the colour `index` and its `color` (`#rrggbb`; high resolution shows 0 as white and 1 as black) |

Palette colours: each 4-bit component is scaled by 17 to 8 bits.

The header, the ST/STE palette word, the colour-cycling fields and the
screen memory layout of the three resolutions follow the NEOchrome format
and the Atari ST/STE video hardware.

## Layout

- `neokit/neo.py` – header, palette, bitplanes
- `neoinfo.py` – command line

## Running

    python3 neoinfo.py examples/sunset.neo examples/desktop.neo
    python3 -m unittest discover -s tests
