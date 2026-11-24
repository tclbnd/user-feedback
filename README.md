# jpegkit

Intake check for photographs and stills that arrive for the picture desk
(`.jpg`). `jpeginfo.py` walks each file's marker segments and prints one JSON
summary, used to check size, coding process, colour sampling and camera data.

## Output

| field | meaning |
|---|---|
| `process` | the coding process of the frame header: `baseline`, `extended`, `progressive`, `lossless`, `differential_sequential`, `differential_progressive`, `differential_lossless`, or the same names prefixed `arithmetic_` for arithmetic coding; `null` without a frame header |
| `width`, `height`, `precision`, `components` | from the frame header (integers) |
| `subsampling` | `gray`, `4:4:4`, `4:2:2`, `4:2:0`, `4:1:1` or `4:4:0` from the first component's sampling factors relative to the others, `null` for any other layout |
| `jfif` | `{"version": "1.02", "units": "none"/"dpi"/"dpcm", "density": [x, y]}` from a JFIF APP0 segment, else `null` |
| `exif` | `{"byte_order": "little"/"big", "make", "model", "orientation", "datetime_original"}` from an Exif APP1 segment (missing values `null`), else `null` |
| `adobe_transform` | `none`, `ycbcr` or `ycck` from an Adobe APP14 segment, else `null` |
| `quant_tables`, `huffman_tables` | the number of tables the DQT and DHT segments define (a segment may hold several) |
| `restart_interval` | from DRI (an integer, 0 without one) |
| `scans`, `restart_markers` | the number of scans, and of restart markers inside the entropy-coded data |
| `eoi`, `trailing_bytes` | `true` when an end-of-image marker is found, and the number of bytes after it |

Marker segments, fill bytes, byte stuffing, restart markers, JFIF and the
TIFF structure inside Exif (IFD0 and the Exif IFD) follow ITU-T T.81, JFIF
1.02 and Exif 2.3.

## Layout

- `jpegkit/jpeg.py` – markers, segments, Exif
- `jpeginfo.py` – command line

## Running

    python3 jpeginfo.py examples/camera.jpg examples/web.jpg
    python3 -m unittest discover -s tests
