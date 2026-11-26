# astro

Check for the Bally Astrocade screen snapshots (`.asv`) the art team saves from
the emulator while redrawing home-console title screens. `astroinfo.py`
rebuilds the 204 active scanlines of the bitmap and prints one JSON summary
per snapshot.

## File layout

`ASTROVR` and a NUL, the sixteen video registers `$00`–`$0F` as last written,
then the 4 KB screen RAM (`$4000`–`$4FFF`). Each output scanline is as wide as
the bitmap mode makes it (one entry per bitmap pixel, not per TV pixel). A
high-resolution snapshot never asks for more bitmap than the 4 KB holds; such a
file is an error. Horizontal borders outside the bitmap are not included.

## Output

| field | meaning |
|---|---|
| `width` | pixels per scanline |
| `scanlines_with_bitmap` | scanlines (of 204) that show screen RAM |
| `colors_used` | distinct colour register values on screen |
| `first_row`, `row_100`, `last_row` | scanlines 0, 100 and 203 as runs `[["hh", count], ...]`, `hh` being the colour register value in lower-case hex |

## Layout

- `astro/screen.py` – registers, colour split, bitmap
- `astroinfo.py` – command line

## Running

    python3 astroinfo.py examples/title.asv examples/maze.asv
    python3 -m unittest discover -s tests
