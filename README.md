# ymzkit

Check for the Yamaha YMZ280B snapshots (`.ymz`) the audio team saves from the
arcade emulator while re-creating soundtracks of late-1990s arcade games. A
snapshot keeps the chip's register file as last written and the sample ROM;
`ymzinfo.py` prints one JSON summary per snapshot of the voices that are keyed
on and what each would play once from its start address to its stop address.

## File layout

`YMZ280B` and a NUL, the 256-byte register file (index = register number, as
last written), the sample ROM length (`u32` little-endian) and the sample ROM.
Bytes past the end of the ROM read as 0.

## Output

`key_on_enable`, `rom_bytes` and `voices`, one object per keyed-on voice in
voice order, with `voice` (1–8), `format` (`"adpcm4"`, `"pcm8"` or
`"pcm16"`), `playing`, `looping`, `fnum`, `rate_hz` (output sample rate, one
decimal), `level`, `left` and `right` (the voice's output level on each side),
the byte addresses `start`, `loop_start`, `loop_end` and `stop`, and for one
pass from start to stop with no looping: `samples` (count), `head` (the first
eight decoded 16-bit samples), `peak` (largest absolute value) and `sum`.
Chip clock 16934400 Hz.

## Layout

- `ymzkit/voice.py` – registers, panning, sample decoding
- `ymzinfo.py` – command line

## Running

    python3 ymzinfo.py examples/stage1.ymz examples/voice.ymz
    python3 -m unittest discover -s tests
