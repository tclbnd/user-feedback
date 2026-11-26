# pokeykit

Check for the POKEY register dumps (`.pkr`) the audio team records from Atari
8-bit games while re-creating their music. The emulator writes the nine
POKEY audio registers once per vertical blank; `pokeyinfo.py` prints one JSON
summary per dump with what each of the four channels played.

## File layout

`POKEYREG`, a flags byte (bit 0 set for a PAL machine, clear for NTSC), a pad
byte and the number of frames (32-bit little-endian), then one 9-byte frame
per vertical blank: AUDF1, AUDC1, AUDF2, AUDC2, AUDF3, AUDC3, AUDF4, AUDC4,
AUDCTL. The machine clock is 1 789 772.5 Hz on NTSC and 1 773 447 Hz on PAL.

## Output

| field | meaning |
|---|---|
| `system` | `"NTSC"` or `"PAL"` |
| `frames`, `complete` | whole frames in the file; `true` when all announced frames are present |
| `frame_rate`, `duration` | vertical blanks per second and seconds, three decimals |
| `channels` | four objects, channel 1 first |

Each channel: `sounding_frames` (frames with a non-zero volume),
`pure_frames` (sounding frames with a pure-tone distortion),
`volume_only_frames`, `filtered_frames` (sounding frames with the high-pass
filter on this channel), and `first_freq`, `lowest_freq`, `highest_freq`:
the output frequency in Hz (three decimals) over the sounding frames whose
pitch is set by the channel's divider, `null` when there are none.

## Layout

- `pokeykit/regs.py` – header, frames, channel frequencies
- `pokeyinfo.py` – command line

## Running

    python3 pokeyinfo.py examples/title.pkr examples/ingame.pkr
    python3 -m unittest discover -s tests
