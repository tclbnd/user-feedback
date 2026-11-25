# sprkit

Check for the C64 sprite snapshots (`.vsp`) the art team saves from the
emulator while redrawing game characters. `sprinfo.py` prints one JSON summary
per snapshot: where each sprite sits on the screen and what the visible text
window looks like with the sprites drawn over the background colour.

## File layout

`C64SPR`, the 47 VIC-II registers `$D000`–`$D02E` as read back from the chip,
the 8 sprite pointers, then the 16 KB VIC bank the pointers refer to. Only the
sprites and the background colour are drawn; text and bitmap graphics are not.

## Output

| field | meaning |
|---|---|
| `complete` | `true` when the whole 16 KB bank is in the file |
| `background` | background colour (0–15) |
| `sprites` | one object per enabled sprite, by sprite number |
| `histogram` | 16 integers: pixels per colour in the 320 × 200 text window after drawing all sprites |
| `colors_used` | how many colours occur in the window |

Each sprite object: `n`, `x` and `y` (position of the sprite's top-left pixel
relative to the top-left pixel of the text window, may be negative), `width`
and `height` (displayed size in pixels), `pixels` (displayed non-transparent
pixels of the sprite before clipping to the window, `null` when its data is
not in the file).

## Layout

- `sprkit/sprites.py` – registers, sprite data, drawing
- `sprinfo.py` – command line

## Running

    python3 sprinfo.py examples/hero.vsp examples/title.vsp
    python3 -m unittest discover -s tests
