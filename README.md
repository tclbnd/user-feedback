# gemkit

Check for GEM bit images (`.img`) from the clip art discs being archived.
`imginfo.py` prints one JSON summary per image so damaged or misconverted
pictures can be found without opening each one in a paint program.

## File layout

The GEM bit image format as written by GEM Paint and its successors: the
big-endian header (version, header length in words, bit planes, pattern
length, pixel size, line width in pixels, number of lines), any header
extension such as the `XIMG` palette, then the compressed scan lines.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `planes` | from the header |
| `rows`, `complete` | scan lines decoded; `true` when all lines are present |
| `palette` | `XIMG` RGB palette as `#rrggbb` (components scaled from 0–1000 with `round(c × 255 / 1000)`), `null` without one |
| `histogram` | pixels per colour index, `2 ** planes` integers; for one plane index 1 is a set (black) pixel |
| `first_row` | the colour indices of the top line, one hex digit per pixel (plane 0 is the lowest bit) |

## Layout

- `gemkit/img.py` – header and scan line decoding
- `imginfo.py` – command line

## Running

    python3 imginfo.py examples/logo.img examples/chart.img
    python3 -m unittest discover -s tests
