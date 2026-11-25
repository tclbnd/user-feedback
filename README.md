# tiakit

Check for Atari 2600 screens recorded by the art team's emulator as scan
line logs (`.tia`). `tiainfo.py` rebuilds the playfield and background the
TIA draws on each line and prints one JSON summary, so a remade level can be
compared with the original cartridge.

## File layout

`TIALOG`, the number of scan lines (16-bit little-endian), then 8 bytes per
line holding the registers as they stood for that whole line: `COLUBK`,
`COLUPF`, `COLUP0`, `COLUP1`, `CTRLPF`, `PF0`, `PF1`, `PF2`. A log cut short
keeps its whole lines only. Each line is the 160 visible pixels as the TIA
shows them from these registers (players, missiles and the ball are not
recorded).

## Output

| field | meaning |
|---|---|
| `lines` | scan line count from the header |
| `complete` | `true` when every line is present |
| `playfield_pixels` | visible pixels drawn by playfield bits over all lines |
| `colors` | `{"0e": n, ...}`: pixels per displayed colour value, two lower-case hex digits, keys ascending |
| `first_line`, `last_line` | the first and last whole line as runs `[["0e", 4], ...]` from left to right |

## Layout

- `tiakit/playfield.py` – playfield drawing
- `tiainfo.py` – command line

## Running

    python3 tiainfo.py examples/maze.tia examples/score.tia
    python3 -m unittest discover -s tests
