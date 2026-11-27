# icokit

Check for the Windows icon (`.ico`) and cursor (`.cur`) files that ship with
old PC games: desktop icons, in-game mouse pointers, tool cursors.
`icoinfo.py` decodes every image in a file the way Windows draws it and prints
one JSON summary per file.

## File layout

Standard Windows icon resource files: a 6-byte header (reserved, type 1 for
icons or 2 for cursors, image count), one 16-byte directory entry per image,
then the images themselves, each either a device-independent bitmap with its
mask or a PNG stream.

## Output

| field | meaning |
|---|---|
| `type` | `icon` or `cursor` |
| `images` | one object per directory entry, in order |

Each image: `width`, `height`, `format` (`bmp` or `png`), for cursors
`hotspot` (`[x, y]`), and for bitmaps `bits` (bits per pixel), `crc32` and
`first_row` (CRC-32, lower-case hex, of the decoded image as RGBA bytes, four
per pixel, rows top to bottom, alpha 0 where the pixel is transparent and 255
where it is not, and of its first row alone) and `transparent` (number of
transparent pixels). PNG images report their size and `null` for the other
fields.

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `icokit/ico.py` – directory and image decoder
- `icoinfo.py` – command line

## Running

    python3 icoinfo.py examples/game.ico examples/sword.cur
    python3 -m unittest discover -s tests
