# qlkit

Check for Sinclair QL screens dumped from the art team's emulator (`.qls`).
`qlinfo.py` prints one JSON summary per dump with colour counts and the top
pixel line, so a converted picture can be compared with how the QL shows it.

## File layout

`QLSCR`, one byte with the display mode the picture was drawn in (`4` or
`8`), then the 32 KB of screen memory from `$20000`: 256 lines of 128 bytes,
top line first, exactly as the QL's display hardware reads it. A dump cut
short keeps its whole lines only.

## Output

| field | meaning |
|---|---|
| `mode` | 4 or 8 |
| `width` | pixels per line in that mode |
| `lines`, `complete` | whole lines in the dump; `true` for all 256 |
| `histogram` | 8 integers: displayed pixels per QL colour 0–7 (0 black, 1 blue, 2 red, 3 magenta, 4 green, 5 cyan, 6 yellow, 7 white) |
| `colors_used` | how many colours occur |
| `flash_bits` | how many flash bits are set |
| `first_line` | the top line, one digit per pixel, left to right |

## Layout

- `qlkit/screen.py` – screen decoding
- `qlinfo.py` – command line

## Running

    python3 qlinfo.py examples/menu.qls examples/game.qls
    python3 -m unittest discover -s tests
