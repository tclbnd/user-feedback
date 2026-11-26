# scdpcm

Check for the Sega CD PCM snapshots (`.scp`) the audio team saves from the
emulator while re-creating game soundtracks. A snapshot holds the RF5C164
registers and wave RAM at one moment; `pcminfo.py` prints one JSON summary
per snapshot with what each of the eight PCM channels plays.

## File layout

`SCDPCM` and two NULs; the channel on/off register (register 8); for each of
the eight channels the ENV, PAN, FDL, FDH, LSL, LSH and ST registers (one byte
each, channel 1 first); then the 64 KB of wave RAM.

## Output

| field | meaning |
|---|---|
| `complete` | `true` when the whole wave RAM is in the file |
| `channels_on` | channels switched on |
| `channels` | eight objects, channel 1 first |

Each channel: `on`; `start` and `loop` (wave RAM addresses); `rate_hz`
(samples played per second, two decimals); `left_gain` and `right_gain`
(pan level × envelope); and, for the samples from the start address up to
the loop marker, `length`, `peak` (largest magnitude, 0–127), `sum` (sum of
the sample values) and `duration` (seconds, four decimals); `loop_length`
is the number of samples from the loop address up to the next loop marker.
Sample fields are `null` when the snapshot is incomplete or the data has no
loop marker.

## Layout

- `scdpcm/chip.py` – registers, wave RAM, channels
- `pcminfo.py` – command line

## Running

    python3 pcminfo.py examples/stage1.scp examples/boss.scp
    python3 -m unittest discover -s tests
