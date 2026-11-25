# raskit

Check for the Sun raster images (`.ras`) kept from the old Sun workstations in
the art archive. `rasinfo.py` prints one JSON summary per file so a converted
copy can be compared with the original picture.

## Input

Files are read exactly as the workstations wrote them: the Sun rasterfile
header, the colour map when there is one, then the image data. Depths 1, 8, 24
and 32 occur, in the old, standard, byte-encoded and RGB types.

Some details of that layout matter when reading the pixels:

- a row is padded up to a 16-bit boundary, so rows of an odd byte length carry
  one pad byte each;
- an `RMT_EQUAL_RGB` colour map is three planes – every red entry, then every
  green, then every blue – not a sequence of RGB triples;
- outside the RGB type the channels of a 24-bit pixel are stored blue, green,
  red, and a 32-bit pixel is a leading unused byte, then blue, green, red;
- byte-encoded rows are run length compressed: `80 00` is a literal `0x80`,
  `80 count value` is `count + 1` copies of `value`.

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
