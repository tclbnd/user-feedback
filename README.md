# pc98kit

Check for the PC-9801 graphics VRAM dumps (`.98g`) the art team saves from the
emulator while redrawing adventure-game scenes. `gvinfo.py` prints one JSON
summary per dump with the colours of the 640 × 400 graphics screen as the
machine displays it.

## File layout

`PC98GV` and two NUL bytes, then a 16-byte block of little-endian fields:

| offset | size | field |
|---|---|---|
| 0 | 1 | bit 0 set: 16-colour analogue mode; clear: 8-colour digital mode |
| 4 | 4 | start address of scroll area 1, as programmed into the graphics GDC |
| 8 | 2 | screen lines in scroll area 1 |
| 10 | 4 | start address of scroll area 2, as programmed into the graphics GDC |

Then the 16 analogue palette entries, three bytes each in the order they are
written to the palette ports `AAh`, `ACh`, `AEh` (low 4 bits used), then the
four 32 KB graphics planes in address order (`A8000h`, `B0000h`, `B8000h`,
`E0000h`). Scroll area 1 is shown from the top of the screen for its line
count and scroll area 2 fills the rest. In 8-colour mode the digital palette
is at its power-on setting. The text layer is not drawn.

## Output

| field | meaning |
|---|---|
| `mode` | `"16-colour"` or `"8-colour"` |
| `palette` | the 16 colours shown for colour codes 0–15, `"#rrggbb"` lower case |
| `complete` | `true` when all four planes are in the file |
| `colors_used` | distinct colours on screen |
| `code_pixels` | 16 integers: screen pixels shown with each colour code |
| `first_row`, `middle_row`, `last_row` | screen lines 0, 200 and 399 as runs `[["#rrggbb", count], ...]` |

4-bit components are widened as `c × 17`. Screen fields are `null` when the
dump is incomplete.

## Layout

- `pc98kit/gvram.py` – header, palette, planes, drawing
- `gvinfo.py` – command line

## Running

    python3 gvinfo.py examples/scene.98g examples/menu.98g
    python3 -m unittest discover -s tests
