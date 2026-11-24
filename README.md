# cpckit

Check for the Amstrad CPC loading screens and art in the retro game archive
(`.scr` screen memory dumps, with or without the AMSDOS file header).
`cpcinfo.py` prints one JSON summary per file, used to verify screens before
they are converted for the web gallery. The screen mode is not stored in the
file, so the pens are given for all three modes.

## Output

| field | meaning |
|---|---|
| `amsdos` | for a file with a valid AMSDOS header: `user`, `filename` (`NAME.EXT`), `type` (`basic`, `protected`, `binary`, else `type_N`), `load` and `execute` (`&XXXX`) and `length` (the 24-bit file length); else `null` |
| `screen_bytes` | bytes after the header (an integer) |
| `points` | for `mode0` (160 × 200, 16 pens), `mode1` (320 × 200, 4 pens) and `mode2` (640 × 200, 2 pens): the pen number at `top_left` (0, 0), `row_1` (0, 1), `row_8` (0, 8), `middle` (int(0.5 × (width − 1)), 100) and `bottom_right` (width − 1, 199) |
| `mode1_pens` | how many of the 320 × 200 mode 1 pixels use pens 0, 1, 2 and 3 |

The screen memory layout, the bit layout of each mode and the AMSDOS header
(including its checksum, which decides whether the first 128 bytes are a
header) follow the Amstrad CPC hardware and AMSDOS. Dumps of 16336 to 16384
screen bytes are accepted.

## Layout

- `cpckit/cpc.py` – header, screen layout, pixel decoding
- `cpcinfo.py` – command line

## Running

    python3 cpcinfo.py examples/loading.scr examples/title_mode0.scr
    python3 -m unittest discover -s tests
