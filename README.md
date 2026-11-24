# gymkit

Catalogue check for the Mega Drive / Genesis soundtrack logs (`.gym`) in the
chip-music archive. `gyminfo.py` prints one JSON summary per log, used to
check length, loop point and the register traffic before conversion.

## Output

| field | meaning |
|---|---|
| `header` | for a file with a GYMX header: `song`, `game`, `publisher`, `emulator`, `dumper`, `comment` (each `null` when empty), `loop_frame` (the stored 1-based loop frame, `null` for 0) and `packed` (`true` when the log data is zlib-compressed); else `null` |
| `frames`, `duration` | the number of frame waits, and the playing time in seconds as a fraction string |
| `ym_port0_writes`, `ym_port1_writes`, `psg_writes` | register writes to the YM2612's two ports and to the PSG |
| `dac_writes` | YM2612 writes to the DAC data register |
| `dac_enabled` | whether the DAC is enabled when the log ends (`false` until enabled) |
| `psg_volume_writes` | writes to each PSG channel's attenuation register, keyed `"0"`–`"3"` (channel 3 is noise) |
| `psg_volumes` | a list of four integers, channel 0 first: each PSG channel's attenuation (0–15) when the log ends, starting from 15 |
| `key_ons` | key-on events (at least one operator on) per FM channel `"1"`–`"6"`, listing only channels with at least one |
| `loop_seconds` | where the loop starts, in seconds as a fraction string (frame 1 is the start of the song); `null` without a loop |

The GYMX header (magic `GYMX`, five 32-byte text fields, a 256-byte comment,
then the loop frame and unpacked size as little-endian uint32), its zlib
compression, the command set, the 60 Hz frame rate, the YM2612 key-on register
(0x28; channel codes 6 and 7 are invalid) and the SN76489 PSG latch byte
(register bits 6:4, bit 4 selects volume) follow the GYM format as Gens writes
it, the YM2612 register map and the SN76489 PSG.

## Layout

- `gymkit/gym.py` – header, commands, statistics
- `gyminfo.py` – command line

## Running

    python3 gyminfo.py examples/green_hill.gym examples/boss.gym
    python3 -m unittest discover -s tests
