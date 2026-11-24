# pcxkit

Intake check for the PCX artwork in the retro game and early-DTP archive.
`pcxinfo.py` prints one JSON summary per image, used to check size, pixel
layout, palettes and the RLE data before conversion.

## Output

| field | meaning |
|---|---|
| `version`, `rle` | the header version (an integer) and whether the data is RLE-encoded |
| `width`, `height` | from the image window (integers) |
| `bits_per_plane`, `planes`, `bytes_per_line` | from the header (integers; `bytes_per_line` is per plane) |
| `padding_bits` | bits per plane line beyond the image width (an integer) |
| `dpi` | `[horizontal, vertical]` from the header |
| `palette` | `vga256` (8-bit, one plane, with the 256-colour palette at the end), `none` (8-bit, one plane, without it), `truecolor` (8-bit, 3 or 4 planes), `ega16` (4 bits per pixel in total, colours from the header palette), `mono` (1 bit), else `other` |
| `grayscale` | `true` when the header's palette type says grey scale |
| `decoded_lines`, `runs`, `complete` | the number of whole scan lines the data decodes to, the number of RLE runs, and whether all lines are there |
| `corner_pixel` | the colour of the top-left pixel as `#rrggbb` (`null` for `none` and `other`, or when no line decodes) |

The header, the RLE scheme (runs may cross plane and line boundaries), plane
order, the EGA header palette and the VGA palette after the `0x0C` marker
follow the ZSoft PCX file format.

## Layout

- `pcxkit/pcx.py` – header, RLE, palettes
- `pcxinfo.py` – command line

## Running

    python3 pcxinfo.py examples/title.pcx examples/sprite.pcx
    python3 -m unittest discover -s tests
