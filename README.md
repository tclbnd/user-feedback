# shrkit

Check for the Apple IIgs super hi-res pictures (32 KiB screen dumps, file
type `$C1`) in the paint program archive. `shrinfo.py` prints one JSON summary
per picture, used to verify the pictures before conversion for the web
gallery.

## Output

| field | meaning |
|---|---|
| `modes` | `{"320": n, "640": n}`: how many lines are in each mode |
| `fill_lines` | 320-mode lines with fill mode on (an integer) |
| `interrupt_lines` | lines with the scan-line interrupt bit set (an integer) |
| `palettes_used` | the palette numbers the lines select, sorted (integers) |
| `points` | the displayed colour (`#rrggbb`) at `top_left` (0, 0), `x1` (1, 0), `x2` (2, 0), `x3` (3, 0), `row_1` (0, 1), `center` (320, 100) and `bottom_right` (639, 199) on a 640 × 200 grid, where a 320-mode pixel covers two columns |
| `colors` | the number of distinct colours shown on the screen (an integer) |

Colour components are 4 bits, widened to 8 bits by × 17. The pixel memory,
the scan-line control bytes, the palettes and how 320 mode, fill mode and
640 mode turn pixel values into colours follow the Apple IIgs video hardware.

## Layout

- `shrkit/shr.py` – lines, palettes, colours
- `shrinfo.py` – command line

## Running

    python3 shrinfo.py examples/title.shr examples/chart.shr
    python3 -m unittest discover -s tests
