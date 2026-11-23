# vgmkit

Catalogue checker for the chiptune archive of VGM logs (`.vgm`): register
writes to the sound chips of consoles and arcade boards, recorded at 44100
samples per second. `vgminfo.py` reads each log and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `version` | header version as `major.minor`, e.g. `1.71` |
| `chips` | sound chips the header declares, by name: `clock` in Hz, `dual` (two of this chip); `YM2610` adds `variant` (`YM2610` or `YM2610B`). Before version 1.10 the YM2413 clock field also stands for the YM2612 and YM2151, so a clock there is reported under all three |
| `total_samples`, `duration` | from the header; `duration` in seconds as a fraction string |
| `samples_counted` | sum of all wait commands in the command data, to check against `total_samples` |
| `commands` | commands in the command data, up to and including the end command |
| `data_blocks`, `data_block_bytes` | data blocks in the command data and their payload bytes |
| `loop` | `start_sample` (samples waited before the loop point), `samples` and `duration` of the loop; `null` for a log without a loop |
| `gd3` | English `title`, `game`, `system`, `author`, plus `date`, `ripper`, `notes` from the GD3 tag, else `null` |
| `chip_writes` | register writes in the command data per chip name, second-chip commands included (0x30 and 0x50 SN76489; 0x51/0xA1 YM2413; 0x52, 0x53, 0xA2, 0xA3 and the 0x80–0x8F write-and-wait commands YM2612; 0x54/0xA4 YM2151; 0x55/0xA5 YM2203; 0x56, 0x57, 0xA6, 0xA7 YM2608; 0x58, 0x59, 0xA8, 0xA9 YM2610; 0x5A/0xAA YM3812; 0xA0 AY8910; 0xB3 GameBoy; 0xB4 NES; 0xC0 SegaPCM) |
| `play_loops` | how many times a player repeats the loop, as a fraction string: 2, multiplied by the loop modifier (header byte 0x7F, divided by 16; 0 means 16) and reduced by the loop base (header byte 0x7E, signed); `null` for a log without a loop |

A header field only counts when the header version has it and it lies before
the start of the command data.

## Layout

- `vgmkit/vgm.py` – header, commands, GD3
- `vgminfo.py` – command line

## Running

    python3 vgminfo.py examples/green_hills.vgm examples/arcade_boss.vgm
    python3 -m unittest discover -s tests
