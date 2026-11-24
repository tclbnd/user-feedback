# gbskit

Catalogue check for the Game Boy soundtrack rips (`.gbs`). `gbsinfo.py` prints
one JSON summary per rip, used to check song numbers, memory layout and the
rate at which the player calls the play routine.

## Output

| field | meaning |
|---|---|
| `version`, `songs`, `first_song` | integers from the header (`first_song` is the song number the player starts with) |
| `title`, `author`, `copyright` | the header strings (Latin-1, up to the first NUL, trimmed), `null` when empty |
| `load_address`, `init_address`, `play_address`, `stack_pointer` | `$XXXX` (upper-case hex) |
| `data_bytes` | the number of code/data bytes after the header (an integer) |
| `end_address` | address of the last data byte, `$XXXX`, `null` without data |
| `timing` | `timer` when the header's timer control enables the timer interrupt, else `vblank` |
| `play_rate` | play routine calls per second as a fraction string |
| `double_speed` | `true` when a timer-driven rip asks for CGB double-speed mode |
| `rom_banks` | the number of 16 KiB ROM banks from address 0 up to the end of the data (an integer, 0 without data) |
| `load_ok` | `true` when the load address is in `$0400`–`$7FFF` |

Header fields, the timer modulo / timer control bytes and the vertical-blank
rate follow the GBS format and the Game Boy hardware (CPU clock 4194304 Hz,
one frame 70224 clocks). Rips with a version other than 1 are rejected.

## Layout

- `gbskit/gbs.py` – header, timing
- `gbsinfo.py` – command line

## Running

    python3 gbsinfo.py examples/awakening.gbs examples/racing.gbs
    python3 -m unittest discover -s tests
