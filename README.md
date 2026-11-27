# qoikit

Check for the QOI ("Quite OK Image") textures and sprites shipped with our
indie game builds. `qoiinfo.py` decodes each image and prints one JSON
summary per file, so a texture pack can be compared against the exporter's
own numbers.

## File layout

The QOI 1.0 format: a 14-byte header (`qoif`, width and height as `u32`
big-endian, channel count 3 or 4, colour space 0 sRGB or 1 linear), the
chunk stream, and the 8-byte end marker.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `channels` | header values |
| `colorspace` | `srgb` or `linear` |
| `ops` | how many chunks of each kind were read: `rgb`, `rgba`, `index`, `diff`, `luma`, `run` |
| `pixels` | pixel count |
| `crc32` | CRC-32 (lower-case hex) of the decoded picture, rows top to bottom, `channels` bytes per pixel (RGB or RGBA) |
| `first_row`, `last_row` | CRC-32 of the first and of the last row alone, same byte layout |

A file that is not a valid QOI image gets an `error` entry instead.

## Layout

- `qoikit/qoi.py` – chunk decoder
- `qoiinfo.py` – command line

## Running

    python3 qoiinfo.py examples/sprite.qoi examples/sky.qoi
    python3 -m unittest discover -s tests
