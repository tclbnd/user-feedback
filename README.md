# pnmkit

Check for Netpbm images (PBM, PGM, PPM and PAM), the plain interchange
formats that scanners, converters and image pipelines write. `pnminfo.py`
decodes every image in each file and prints one JSON summary per file.

## Formats

`P1`/`P4` PBM, `P2`/`P5` PGM, `P3`/`P6` PPM (plain and raw), `P7` PAM, as
defined by the Netpbm format specifications.

## Output

| field | meaning |
|---|---|
| `images` | one object per image in the file |
| `format` | magic number, e.g. `P6` |
| `width`, `height`, `maxval` | header values (`maxval` is 1 for PBM) |
| `alpha` | whether the image has an alpha channel |
| `crc32` | CRC-32 (lower-case hex) of the image as 8-bit RGB, three bytes per pixel, rows top to bottom: samples scaled to 0–255 as `round(sample × 255 / maxval)` (halves round up), grey images repeated into R, G and B, alpha left out, black-and-white pixels as 0 (black) or 255 (white) |
| `colours` | number of distinct RGB colours |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `pnmkit/pnm.py` – header and raster reader
- `pnminfo.py` – command line

## Running

    python3 pnminfo.py examples/photo.ppm examples/scan.pgm examples/fax.pbm
    python3 -m unittest discover -s tests
