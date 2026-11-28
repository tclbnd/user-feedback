# pickit

Picture check for PC Paint / Pictor images (`.pic`), the format many DOS games
and their art tools used for title screens, backgrounds and sprite sheets.
`picinfo.py` decodes each picture the way PC Paint shows it and prints one
JSON summary per file.

## File layout

Standard PC Paint / Pictor files: a header with the magic number 0x1234,
width, height, screen offsets and a byte giving bits per plane (low four
bits) and planes minus one (high four bits); then an optional palette
extension (type and size, followed by CGA, EGA or VGA palette data); then
the bitmap, either raw or as blocks of run-length data, each block with its
own size and marker byte.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `planes`, `bits` | number of planes and total bits per pixel |
| `palette_type` | palette extension type (−1 when there is none) |
| `crc32` | CRC-32 (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom, colours as the PC shows them |
| `first_row` | CRC-32 of the top row alone, same byte layout |
| `colours` | number of distinct palette indices used |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `pickit/pictor.py` – header, palette and bitmap decoder
- `picinfo.py` – command line

## Running

    python3 picinfo.py examples/title.pic examples/sprites.pic
    python3 -m unittest discover -s tests
