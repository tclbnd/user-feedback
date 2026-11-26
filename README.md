# dmckit

Check for the NES DMC sample snapshots (`.ndmc`) the audio team saves from the
emulator while re-recording drum and voice samples. `dmcinfo.py` prints one
JSON summary per snapshot: where the sample is, how long and how fast it
plays, and how the channel's output level moves while it plays once.

## File layout

`NESDMC`, a region byte (0 NTSC, 1 PAL), the four DMC registers `$4010`,
`$4011`, `$4012`, `$4013` as last written, then the 32 KB of PRG space
`$8000`–`$FFFF` the DMC reads from.

## Output

| field | meaning |
|---|---|
| `region` | `"NTSC"` or `"PAL"` |
| `address`, `length` | first sample byte (`"$XXXX"`) and sample length in bytes |
| `complete` | `true` when every sample byte is in the file |
| `period`, `rate_hz` | CPU cycles per output bit and the resulting bit rate (two decimals) |
| `duration` | seconds to play the sample once, six decimals |
| `loop`, `irq` | the two flags in `$4010` |
| `initial_level` | output level loaded from `$4011` |
| `final_level`, `min_level`, `max_level` | output level after the sample, and its range while playing |
| `level_changes` | how many output bits actually changed the level |

CPU clocks: NTSC 1789773 Hz, PAL 1662607 Hz.

## Layout

- `dmckit/dmc.py` – registers, sample playback
- `dmcinfo.py` – command line

## Running

    python3 dmcinfo.py examples/snare.ndmc examples/voice.ndmc
    python3 -m unittest discover -s tests
