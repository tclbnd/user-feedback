# sc2kit

Check for the MSX SCREEN 2 pictures (`.sc2`, saved with `BSAVE ...,S`) in the
retro art archive. `sc2info.py` prints one JSON summary per file, used to
verify the pictures before they are converted for the web gallery.

## Output

| field | meaning |
|---|---|
| `start`, `end`, `execute` | the BSAVE header addresses as `&HXXXX` |
| `vram_bytes` | VRAM bytes the file holds (an integer) |
| `points` | the colour name at `top_left` (0, 0), `x7` (7, 0), `row_1` (0, 1), `row_64` (0, 64), `center` (128, 96) and `bottom_right` (255, 191) |
| `sprites` | sprites in the sprite attribute table before the end-of-list marker |

Colour names, by TMS9918 colour number 0–15: `transparent`, `black`,
`medium_green`, `light_green`, `dark_blue`, `light_blue`, `dark_red`, `cyan`,
`medium_red`, `light_red`, `dark_yellow`, `light_yellow`, `dark_green`,
`magenta`, `gray`, `white`. Bytes the file does not cover read as 0.

The BSAVE header, the SCREEN 2 VRAM layout (pattern generator, name,
sprite attribute and colour tables) and how a pixel picks its colour follow
the MSX BASIC BSAVE format and the TMS9918 Graphics II mode.

## Layout

- `sc2kit/sc2.py` – header, VRAM tables, colours, sprites
- `sc2info.py` – command line

## Running

    python3 sc2info.py examples/title.sc2 examples/map.sc2
    python3 -m unittest discover -s tests
