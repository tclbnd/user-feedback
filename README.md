# chanf

Check for the Fairchild Channel F port-write logs (`.cflog`) the art team
records from the emulator while redrawing cartridge title screens.
`cfinfo.py` replays each log into the 128 × 64 video RAM, colours it as the
console does and prints one JSON summary per log.

## File layout

`CHFLOG` and two NUL bytes, a video standard byte (0 NTSC, 1 PAL), three pad
bytes, the number of port writes (`u32` little-endian), then the writes in the
order the game made them, two bytes each: port number and value. Writes to
ports other than 0, 1, 4 and 5 are ignored. Video RAM starts cleared to 0.

## Output

| field | meaning |
|---|---|
| `writes` | port writes in the log |
| `plotted` | pixels actually written into video RAM |
| `row_palettes` | 4 integers: video RAM rows using each palette |
| `colors_used` | distinct colours in the 128 × 64 image |
| `first_row`, `row_32`, `last_row` | rows 0, 32 and 63 of the full 128-column image as runs `[["#rrggbb", count], ...]` |

The console's eight colours are black `#101010`, white `#fdfdfd`, red
`#ff3153`, green `#02cc5d`, blue `#4b3ff3`, light grey `#e0e0e0`, light green
`#91ffa6` and light blue `#ced0ff`. The image is the whole video RAM, one pixel
per cell, including the palette columns.

## Layout

- `chanf/vram.py` – port replay, palettes, colours
- `cfinfo.py` – command line

## Running

    python3 cfinfo.py examples/title.cflog examples/maze.cflog
    python3 -m unittest discover -s tests
