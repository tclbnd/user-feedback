# wsvdp

Check for the WonderSwan and WonderSwan Color display snapshots (`.wsv`) the
art team saves from the emulator while redrawing handheld screens.
`wsinfo.py` rebuilds the 224 × 144 screen and prints one JSON summary per
snapshot.

## File layout

`WSVDP` and three NUL bytes, the I/O ports `$00`–`$3F` as last written, port
`$60` and a pad byte, then the 64 KB of VRAM (16 KB is enough for a mono
WonderSwan). Port `$00` bits 0–2 enable SCR1, SCR2 and sprites; the windows are
never enabled in these snapshots. Layers stack backdrop, SCR1, sprites without
the priority bit, SCR2, sprites with the priority bit; lower-numbered sprites
are in front of higher-numbered ones. Packed tiles only occur in 16-colour
mode.

The backdrop is port `$01`: in mono mode its low three bits pick a shade pool
entry, in colour mode the high nibble is the palette and the low nibble the
colour.

## Output

| field | meaning |
|---|---|
| `mode` | `"mono"`, `"colour-4"`, `"colour-16"` or `"colour-16-packed"` |
| `colors_used` | distinct colours on screen |
| `sprite_pixels` | screen pixels where a sprite is the visible layer |
| `first_row`, `row_72`, `last_row` | lines 0, 72 and 143 as runs `[["#rrggbb", count], ...]` |

4-bit values are widened as `v × 17`. A VRAM image shorter than the mode needs
is an error.

## Layout

- `wsvdp/screen.py` – ports, palettes, tiles, layers
- `wsinfo.py` – command line

## Running

    python3 wsinfo.py examples/puzzle.wsv examples/rpg.wsv
    python3 -m unittest discover -s tests
