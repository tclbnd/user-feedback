# snkkit

Check for the SNK wave generator snapshots (`.snkw`) the audio team saves from
the arcade emulator while re-creating early SNK soundtracks. Each voice is the
single programmable-waveform generator of one board; `snkinfo.py` prints one
JSON summary per snapshot.

## File layout

`SNKWAVE` and a NUL, a count of voices, then six bytes per voice: the
registers F1, F2, W3, W4, W5 and W6 as last written.

## Output

`voices`, one object per voice with `voice`, `frequency` (the generator's
12-bit frequency value), `silent`, `cycle_hz` (one whole 16-step waveform per
second, three decimals, `null` when silent), `wave` (the 16 output levels of
one waveform cycle, first step first, in the generator's signed output units)
and `wave_min`, `wave_max`, `wave_sum`, and `pcm`: the generator's audio output
from reset (step counter and waveform position 0), one output sample every 256
generator clocks, first 512 samples, as signed 16-bit values: `rate_hz` (one
decimal), `samples`, `peak` (largest absolute value), `sum` and `crc32` (of the
samples as 16-bit little-endian, lower-case hex). Generator clock 6000000 Hz.

## Layout

- `snkkit/wave.py` – registers, frequency, waveform
- `snkinfo.py` – command line

## Running

    python3 snkinfo.py examples/vanguard.snkw examples/fantasy.snkw
    python3 -m unittest discover -s tests
