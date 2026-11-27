# k7232kit

Check for the Konami K007232 sound snapshots (`.k7232`) the audio team saves
from the arcade emulator while re-recording Contra, Gradius II and TMNT drums.
A snapshot freezes the two PCM channels at the moment they are started;
`k7232info.py` prints one JSON summary per snapshot.

## File layout

`K007232S`, chip registers 0–13 as last written (six per channel: step low,
step high, start low, start middle, start high, key-on; register 12 is the
external port, register 13 the loop flags), one byte whose bits 0 and 1 tell
whether channel 1 and channel 2 were started, then the sample ROM starting at
address 0.

## Output

`rom_size` and `channels`, one object per channel with `channel`, `keyed`,
`step`, `start` (ROM address), `rate_hz` (sample bytes played per second, two
decimals), `loop`, and for a keyed channel: `length` (sample bytes played
before the sample ends), `peak` and `sum` (of the signed sample values) and
`terminated` (`true` when the sample ends inside the ROM). Chip clock
3579545 Hz.

## Layout

- `k7232kit/pcm.py` – registers, channels, sample ROM
- `k7232info.py` – command line

## Running

    python3 k7232info.py examples/snare.k7232 examples/bass.k7232
    python3 -m unittest discover -s tests
