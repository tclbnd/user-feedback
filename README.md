# pcvid

Check for the IBM PC graphics screens (`.pcv`) the art team dumps from the
emulator while redrawing old DOS games. `vidinfo.py` prints one JSON summary
per dump with the colours the monitor shows.

## File layout

`PCVID`, the BIOS video mode (4, 5 or 6 for CGA, `0Dh` for EGA 320 × 200 × 16,
`80h` for the Hercules graphics page), the CGA colour select register (port
`3D9h`), the 16 EGA attribute-controller palette registers, then video memory
exactly as the adapter scans it: the 16 KB CGA buffer at `B800:0000`, the four
EGA bit planes (8000 bytes each, plane 0 first) or the 32 KB Hercules page.

## Output

| field | meaning |
|---|---|
| `mode`, `width`, `height` | mode and picture size in pixels |
| `complete` | `true` when the whole buffer is in the dump |
| `colors` | `{"#rrggbb": n, ...}` pixels per displayed colour, keys ascending |
| `colors_used` | how many colours occur |
| `first_line`, `last_line` | top and bottom lines as runs `[["#rrggbb", count], ...]` |

Colours are those of an IBM CGA/EGA colour monitor (`#aa`/`#55` levels);
Hercules is shown as black and white. Screen fields are `null` when the dump
is incomplete.

## Layout

- `pcvid/video.py` – modes, palettes, memory layout
- `vidinfo.py` – command line

## Running

    python3 vidinfo.py examples/keen.pcv examples/flight.pcv
    python3 -m unittest discover -s tests
