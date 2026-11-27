# gxkit

Check for the Galaxian-hardware screen snapshots (`.gxs`) the team saves from
the arcade emulator while re-drawing the screens of Galaxian, Moon Cresta and
their clones. `gxinfo.py` renders the frame the board would show (background
characters, the 8 sprites and the bullets; stars are not part of the
snapshot) and prints one JSON summary per snapshot.

## File layout

`GALAXSNP`, then the 1 KB of video RAM ($5000–$53FF, one character code per
cell, 32 cells per row), the 256 bytes of object RAM ($5800–$58FF) and the
4 KB graphics ROM as the two 2 KB ROMs (1H then 1K) back to back. Any other
length is an error.

## Picture

Coordinates are the board's own, before the monitor is turned on its side:
256 pixels across, and the 224 visible lines 16–239 of the 256-line frame.
The picture keeps the colour number of each pixel: `palette * 4 + pen` for
characters and sprites (pen 0 is transparent and leaves the background,
number 0), 32 for a shell and 33 for the missile.

## Output

| field | meaning |
|---|---|
| `tiles_used` | cells of the 32 × 32 video RAM whose character is not blank |
| `column_colours` | the palette (0–7) of each of the 32 character columns |
| `sprites` | `[number, x, y]` of every sprite with at least one pixel in the picture, by number; `y` is relative to line 16 |
| `bullet_pixels` | pixels showing a shell or the missile |
| `colours_used` | number of distinct colour numbers in the picture |
| `picture_crc32` | CRC-32 of the 256 × 224 picture, one byte per pixel, line by line, lower-case hex |

## Layout

- `gxkit/galaxian.py` – snapshot reader and renderer
- `gxinfo.py` – command line

## Running

    python3 gxinfo.py examples/attract.gxs examples/wave.gxs
    python3 -m unittest discover -s tests
