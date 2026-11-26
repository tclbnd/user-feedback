# maria

Check for the Atari 7800 snapshots (`.a78s`) the art team saves from the
emulator while redrawing 7800 title screens. `frameinfo.py` rebuilds the
frame MARIA draws from the display list list and prints one JSON summary per
snapshot.

## File layout

`A7800MS` and a NUL, the 32 MARIA registers `$20`–`$3F` as last written, then
the 64 KB memory image as MARIA sees it (`$0000`–`$FFFF`).

The frame is the first 243 lines the display list list produces (NTSC), each
160 pixels wide, starting from the DLL entry the registers point at. Every
object in these snapshots is a direct 160A object (read mode 160A/B, write
mode clear); character (indirect) mode and holey DMA are not used. Horizontal
positions wrap at 256 and only positions 0–159 are visible. Pixel value 0 is
transparent and shows the background colour.

## Output

| field | meaning |
|---|---|
| `zones` | DLL entries used for the 243 lines |
| `objects` | display list headers over those zones |
| `dli_zones` | zones whose DLL entry requests a display list interrupt |
| `colors_used` | distinct colour register values in the frame |
| `background_pixels` | frame pixels not covered by any object pixel |
| `first_row`, `row_100`, `last_row` | lines 0, 100 and 242 as runs `[["hh", count], ...]`, `hh` the colour register value in lower-case hex |

## Layout

- `maria/frame.py` – registers, DLL, display lists, 160A drawing
- `frameinfo.py` – command line

## Running

    python3 frameinfo.py examples/title.a78s examples/stage.a78s
    python3 -m unittest discover -s tests
