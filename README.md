# raskit

Check for the Sun raster images (`.ras`) kept from the old Sun workstations in
the art archive. `rasinfo.py` prints one JSON summary per file so a converted
copy can be compared with the original picture.

## Input

Files are read exactly as the workstations wrote them: the Sun rasterfile
header, the colour map when there is one, then the image data. Depths 1, 8, 24
and 32 occur, in the old, standard, byte-encoded and RGB types.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `depth`, `type` | from the header (integers) |
| `colormap_entries` | number of colour map entries, `0` without a map |
| `row_bytes` | bytes one image row occupies in the image data |
| `rows`, `complete` | whole rows present; `true` when all `height` rows are |
| `colors_used` | number of distinct displayed colours over those rows |
| `first_row` | up to the first 8 displayed colours of the top row, `"#rrggbb"` lower case |
| `last_pixel` | displayed colour of the last pixel of the last whole row, `null` when there is none |

Without a colour map, a 1-bit pixel set to 1 is black and 0 is white, and an
8-bit pixel is a grey level.

## Layout

- `raskit/raster.py` – header, colour map, pixels
- `rasinfo.py` – command line

## Running

    python3 rasinfo.py examples/logo.ras examples/photo.ras
    python3 -m unittest discover -s tests
