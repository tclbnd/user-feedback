# fitskit

Check for the FITS files (`.fits`) coming off the observatory's camera and
reduction pipeline. `fitsinfo.py` prints one JSON summary per file, used to
catch truncated frames and suspicious pixel ranges before archiving.

## Output

| field | meaning |
|---|---|
| `hdus` | number of header-data units read |
| `extensions` | `XTENSION` of every extension in order (`IMAGE`, `BINTABLE`, …) |
| `hdu` | one object per HDU, primary first |

Each HDU has:

| field | meaning |
|---|---|
| `type` | `primary`, or the extension's `XTENSION` |
| `bitpix` | `BITPIX` (integer) |
| `shape` | `[NAXIS1, NAXIS2, …]` (empty when `NAXIS` is 0) |
| `data_bytes` | size of the data unit in bytes, padding not included |
| `header_offset` | file offset where the HDU's header starts |
| `object` | the `OBJECT` keyword's value, `null` without one |
| `complete` | `true` when the file holds the whole data unit |
| `min`, `max` | smallest and largest physical pixel value (`%.6g` strings), for the primary HDU and `IMAGE` extensions with data; `null` otherwise |
| `blank` | how many pixels are undefined (`BLANK` for integer data, NaN for floating point), `null` when no statistics are given |

Physical values are `BZERO + BSCALE × stored value`. Header cards, keyword
values, the data unit layout and the block structure follow the FITS
Standard 4.0.

## Layout

- `fitskit/fits.py` – headers, HDUs, pixel statistics
- `fitsinfo.py` – command line

## Running

    python3 fitsinfo.py examples/m31.fits examples/reduced.fits
    python3 -m unittest discover -s tests
