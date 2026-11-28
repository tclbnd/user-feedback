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

## Decoding notes

- Bitmap rows are stored bottom-up; output is top-down, as Windows draws it.
- Packed 1/4/8bpp pixels run most-significant bit first.
- Colour rows and AND-mask rows are DWORD aligned.
- For 1/4/8/24bpp images the AND mask marks transparent pixels. A 32bpp
  image with a non-zero alpha channel uses alpha instead (any non-zero alpha
  counts as opaque); if all alpha bytes are zero, the mask is used, and if
  there is no mask either the image is fully opaque.
- In cursor files the directory entry's `wPlanes` and `wBitCount` fields hold
  the hotspot `[x, y]`.

## Layout

- `icokit/ico.py` – directory and image decoder
- `icoinfo.py` – command line

## Running

    python3 icoinfo.py examples/game.ico examples/sword.cur
    python3 -m unittest discover -s tests
