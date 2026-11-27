# m7kit

Check for the SNES Mode 7 snapshots (`.m7`) the art team saves from the
emulator while redrawing racing tracks and map screens. A snapshot keeps the
Mode 7 registers as last written and the VRAM; `m7info.py` renders the
256 × 224 BG1 frame those registers give (no HDMA, mosaic or windows; the same
registers on every line) and prints one JSON summary per snapshot.

## File layout

`SNESM7` and two NULs, then little-endian fields: M7SEL (`u8`), M7A, M7B,
M7C, M7D, M7X, M7Y, M7HOFS, M7VOFS (`u16` each, the 16-bit register values as
the CPU wrote them), the VRAM length (`u32`) and the VRAM bytes (byte address
order, low byte of word 0 first). VRAM past the given length reads as 0.

## Output

| field | meaning |
|---|---|
| `screen_over` | what is shown outside the 1024 × 1024 map: `"wrap"`, `"transparent"` or `"tile0"` |
| `hflip`, `vflip` | screen flips |
| `matrix` | A, B, C, D as decimal values, four decimals |
| `center`, `scroll` | `[x, y]` as signed values |
| `opaque` | pixels with a non-zero palette index |
| `histogram` | `{"index": count}` for every non-zero palette index, ascending |
| `samples` | palette index at (0,0), (255,0), (0,223), (255,223), (128,112), (37,191), (200,50) |
| `crc32` | CRC-32 of the 256 × 224 palette indices, row by row, lower-case hex |

## Layout

- `m7kit/frame.py` – registers and the Mode 7 renderer
- `m7info.py` – command line

## Running

    python3 m7info.py examples/track.m7 examples/worldmap.m7
    python3 -m unittest discover -s tests
