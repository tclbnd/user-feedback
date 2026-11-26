# uvi

Check for the Emerson Arcadia 2001 screen snapshots (`.arc`) the art team saves
from the emulator while redrawing cartridge screens. `uviinfo.py` rebuilds the
128-pixel-wide character screen of the Signetics 2637 UVI and prints one JSON
summary per snapshot.

## File layout

`ARCADIA` and a NUL, the UVI memory `$1800`–`$1AFF` as the CPU last left it,
then the console's 56 built-in character glyphs (codes `$00`–`$37`, 8 bytes
each, top row first, bit 7 leftmost) as the character generator draws them.
Characters are always 8 × 8 here (no double height); a snapshot with double
height is an error. Sprites, scrolling and the start line are not part of this
check. Without the 26-row setting only the upper 13 rows are drawn and the
lower 13 show the screen colour.

## Output

| field | meaning |
|---|---|
| `rows` | 13 or 26 character rows displayed |
| `multicolour` | whether multicolour mode is on |
| `cells` | object counting drawn cells as `text` or as block `blocks` |
| `colors_used` | distinct colours on screen |
| `first_line`, `line_104`, `last_line` | pixel lines 0, 104 and 207 as runs `[["#rrggbb", count], ...]` |

Colours 0–7 are white, yellow `#ffff00`, cyan `#00ffff`, green `#00ff00`,
magenta `#ff00ff`, red `#ff0000`, blue `#0000ff` and black.

## Layout

- `uvi/screen.py` – screen memory, glyphs, colours
- `uviinfo.py` – command line

## Running

    python3 uviinfo.py examples/title.arc examples/invaders.arc
    python3 -m unittest discover -s tests
