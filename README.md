# amiscr

Check for the Amiga screen dumps (`.ascr`) the art team saves from the
emulator while redrawing game and demo screens. `amiinfo.py` prints one JSON
summary per dump with the colours the OCS chipset puts on screen.

## File layout

`AMISCR`, then (big-endian) BPLCON0, BPLCON2, width and height in pixels, the
32 colour registers COLOR00–COLOR31 (`0x0RGB`), then the bitplanes one after
another, plane 1 first, each `height` rows of whole 16-bit words.

## Output

| field | meaning |
|---|---|
| `mode` | `"normal"`, `"ehb"`, `"ham6"` or `"dpf"` (dual playfield), as the chipset displays the dump |
| `planes`, `width`, `height` | from the header |
| `complete` | `true` when every bitplane is in the file |
| `colors_used` | distinct colours on screen |
| `first_line`, `last_line` | top and bottom lines as runs `[["#rrggbb", count], ...]` |

4-bit components are widened to 8 bits as `c × 17`. Screen fields are `null`
when the dump is incomplete.

## Layout

- `amiscr/screen.py` – registers, bitplanes, display modes
- `amiinfo.py` – command line

## Running

    python3 amiinfo.py examples/demo_ham.ascr examples/game_dpf.ascr
    python3 -m unittest discover -s tests
