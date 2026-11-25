# tiakit

Check for Atari 2600 screens recorded by the art team's emulator as scan
line logs (`.tia`). `tiainfo.py` rebuilds the background, playfield, players,
missiles and ball the TIA draws on each line and prints one JSON summary, so a remade level can be
compared with the original cartridge.

## File layout

`TIALOG`, the number of scan lines (16-bit little-endian), then 22 bytes per
line holding the registers as they stood for that whole line: `COLUBK`,
`COLUPF`, `COLUP0`, `COLUP1`, `CTRLPF`, `PF0`, `PF1`, `PF2`, `GRP0`, `GRP1`,
`NUSIZ0`, `NUSIZ1`, `REFP0`, `REFP1`, `ENAM0`, `ENAM1`, `ENABL`, then the
horizontal positions (0–159, the leftmost visible pixel is 0) of player 0,
player 1, missile 0, missile 1 and the ball. A log cut short keeps its whole
lines only. Each line is the 160 visible pixels as the TIA shows them from
these registers.

## Output

| field | meaning |
|---|---|
| `lines` | scan line count from the header |
| `complete` | `true` when every line is present |
| `playfield_pixels` | pixels covered by playfield bits over all lines (whether or not an object is drawn over them) |
| `colors` | `{"0e": n, ...}`: pixels per displayed colour value, two lower-case hex digits, keys ascending |
| `first_line`, `last_line` | the first and last whole line as runs `[["0e", 4], ...]` from left to right |

## Layout

- `tiakit/playfield.py` – playfield drawing
- `tiainfo.py` – command line

## Running

    python3 tiainfo.py examples/maze.tia examples/score.tia
    python3 -m unittest discover -s tests
