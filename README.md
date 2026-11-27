# snkkit

Check for the SNK wave generator snapshots (`.snkw`) the audio team saves from
the arcade emulator while re-creating early SNK soundtracks. Each voice is the
single programmable-waveform generator of one board; `snkinfo.py` prints one
JSON summary per snapshot.

## File layout

`SNKWAVE` and a NUL, a count of voices, then six bytes per voice: the
registers F1, F2, W3, W4, W5 and W6 as last written. F1 holds the high six
bits of the 12-bit phase increment and F2 the low six bits (the top two data
lines of each latch are unconnected); W3..W6 hold the eight programmable
waveform steps, two 4-bit levels per byte with the high nibble being the
earlier step.

## Generator rules

The increment is added to a 12-bit accumulator every generator clock; each
overflow advances the 16-step waveform position by one. A larger increment is
a higher pitch, and a zero increment freezes the position. Only the first
eight waveform steps are programmable; the other eight play silence. Levels
are offset-binary on four bits, so the signed output level is
`(nibble - 8) * 16` (range −128..112). One output sample is taken every 256
generator clocks, with the accumulator and waveform position starting at zero.

A voice is `silent` only when its rendered output is all zero (e.g. a zero
increment whose frozen step 0 is the mid-scale level). A zero increment with a
non-zero frozen level is an audible constant: it is not silent, but it has no
pitch, so `cycle_hz` is still `null`.

## Output

`voices`, one object per voice with `voice`, `frequency` (the generator's
12-bit phase increment), `silent`, `cycle_hz` (one whole 16-step waveform per
second, three decimals, `null` when no pitch can be computed), `wave` (the 16
output levels of one waveform cycle, first step first, in the generator's
signed output units) and `wave_min`, `wave_max`, `wave_sum`, and `pcm`: the
generator's audio output from reset (step counter and waveform position 0),
one output sample every 256 generator clocks, first 512 samples, as signed
16-bit values: `rate_hz` (one decimal), `samples`, `peak` (largest absolute
value), `sum` and `crc32` (of the samples as 16-bit little-endian, lower-case
hex). Generator clock 6000000 Hz.

## Layout

- `snkkit/wave.py` – registers, frequency, waveform
- `snkinfo.py` – command line

## Running

    python3 snkinfo.py examples/vanguard.snkw examples/fantasy.snkw
    python3 -m unittest discover -s tests
