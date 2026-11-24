# spukit

Check for the Spectrum 512 pictures (`.spu`) in the Atari ST demo archive.
`spuinfo.py` prints one JSON summary per picture, used to verify the
pictures before they are converted for the web gallery.

## Output

| field | meaning |
|---|---|
| `bytes` | file size (always 51104) |
| `points` | the displayed colour (`#rrggbb`) at `line_0` (5, 0), `top_left` (0, 1), `x1` (1, 1), `x160` (160, 40), `x161` (161, 40), `center` (160, 100) and `bottom_right` (319, 199) on the 320 × 200 screen |
| `index_counts` | how many pixels of the bitmap use colour index 0–15 (16 integers) |
| `colors_shown` | the number of distinct ST colours shown on lines 1–199 (an integer) |
| `palette_changes` | the number of lines 2–199 whose 48-colour palette differs from the line above (an integer) |

Colours are ST 3-bit components widened to 8 bits as `round(v × 255 / 7)`.
The bitmap layout, the per-line palettes and the way a colour index picks
one of its line's 48 colours follow the Spectrum 512 display, as the
Spectrum 512 viewer shows it on a ST.

## Layout

- `spukit/spu.py` – bitmap, palettes, colours
- `spuinfo.py` – command line

## Running

    python3 spuinfo.py examples/plasma.spu examples/logo.spu
    python3 -m unittest discover -s tests
