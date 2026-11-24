# gymkit

Catalogue check for the Mega Drive / Genesis soundtrack logs (`.gym`) in the
chip-music archive. `gyminfo.py` prints one JSON summary per log, used to
check length, loop point and the register traffic before conversion.

## Output

| field | meaning |
|---|---|
| `header` | for a file with a GYMX header: `song`, `game`, `publisher`, `emulator`, `dumper`, `comment` (`null` when empty), `loop_frame` (the stored loop frame, `null` for 0) and `packed` (`true` when the log data is compressed); else `null` |
| `frames`, `duration` | the number of frame waits, and the playing time in seconds as a fraction string |
| `ym_port0_writes`, `ym_port1_writes`, `psg_writes` | register writes to the YM2612's two ports and to the PSG |
| `dac_writes` | YM2612 writes to the DAC data register |
| `key_ons` | key-on events (at least one operator on) per FM channel `"1"`–`"6"` |
| `loop_seconds` | where the loop starts, in seconds as a fraction string (frame 1 is the start of the song); `null` without a loop |

The GYMX header, its compression, the command set, the frame rate and the
YM2612 key-on register follow the GYM format as Gens writes it and the
YM2612 register map.

## Layout

- `gymkit/gym.py` – header, commands, statistics
- `gyminfo.py` – command line

## Running

    python3 gyminfo.py examples/green_hill.gym examples/boss.gym
    python3 -m unittest discover -s tests
