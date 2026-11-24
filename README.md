# avrkit

Check for the Atari ST sampler library (`.avr` files from Audio Visual
Research compatible samplers). `avrinfo.py` prints one JSON summary per
sample, used to check the format, loop settings and level before the samples
are converted.

## Output

| field | meaning |
|---|---|
| `name` | the 8-character name followed by the extended name, `null` when both are empty |
| `channels`, `bits` | channel count and bits per sample (integers) |
| `signed` | `true` for signed sample data |
| `sample_rate` | in Hz (an integer) |
| `frames`, `duration` | sample frames from the header, and seconds as a fraction string |
| `loop` | `{"start", "end"}` in frames when the sample loops, else `null` |
| `midi_note` | for `0xFFnn` the note `nn`; for other values except `0xFFFF` (no note) the high byte; else `null` |
| `complete` | `true` when the file holds all the frames |
| `peak` | the largest sample magnitude as a fraction of full scale, `%.6f` |

The 128-byte header (its flag values, the sample rate field and the names)
and the sample data layout follow the AVR format.

## Layout

- `avrkit/avr.py` – header, samples
- `avrinfo.py` – command line

## Running

    python3 avrinfo.py examples/strings_c3.avr examples/snare.avr
    python3 -m unittest discover -s tests
