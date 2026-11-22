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

A header field only counts when the header version has it and it lies before
the start of the command data.

## Layout

- `vgmkit/vgm.py` – header, commands, GD3
- `vgminfo.py` – command line

## Running

    python3 vgminfo.py examples/green_hills.vgm examples/arcade_boss.vgm
    python3 -m unittest discover -s tests
