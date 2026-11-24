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
| `dac_enabled` | whether the DAC is enabled when the log ends (`false` until enabled) |
| `psg_volume_writes` | writes to each PSG channel's attenuation register, keyed `"0"`–`"3"` (channel 3 is noise) |
| `psg_volumes` | each PSG channel's attenuation (0–15) when the log ends, starting from 15 |
| `key_ons` | key-on events (at least one operator on) per FM channel `"1"`–`"6"` |
| `loop_seconds` | where the loop starts, in seconds as a fraction string (frame 1 is the start of the song); `null` without a loop |

The GYMX header, its compression, the command set, the frame rate and the
YM2612 key-on register follow the GYM format as Gens writes it, the YM2612
register map and the SN76489 PSG.

## Format notes

- A GYMX header is 0x1AC bytes: magic, song, game, publisher, emulator,
  dumper (32 bytes each), a 256-byte comment, then the one-based loop frame
  and the unpacked size as little-endian 32-bit words. The payload at
  0x1AC is a zlib stream when packed, otherwise the raw command stream.
- Commands: `0x00` waits one NTSC frame (`1/60` s); `0x01`/`0x02` write a
  register to YM2612 port 0/1 and take two bytes; `0x03` writes one latched
  byte to the SN76489.
- YM2612 `0x28` is the key-on register (port 0): bits 7-4 select operators,
  the low three bits select FM channel 1-6, and codes 6-7 are invalid.
  `0x2A` holds DAC data and `0xB6` bit 7 enables the DAC.
- SN76489 bytes with bit 7 set are latches: bits 6-5 select channel 0-3
  (3 is the noise channel), bit 4 selects the attenuation register and the
  low four bits are the attenuation (0 loudest, 15 silent).

## Layout

- `gymkit/gym.py` – header, commands, statistics
- `gyminfo.py` – command line

## Running

    python3 gyminfo.py examples/green_hill.gym examples/boss.gym
    python3 -m unittest discover -s tests
