# gf1pat

Check for the Gravis UltraSound GF1 patches (`.pat`) in the music team's MIDI
instrument set. `patinfo.py` prints one JSON summary per patch with the
samples of its first layer as the GF1 plays them.

## Output

| field | meaning |
|---|---|
| `description` | the description text up to the first NUL |
| `complete` | `false` when a sample header or its data is cut short |
| `samples` | list, one object per sample of the first layer, in file order |

Each sample object:

| field | meaning |
|---|---|
| `name` | wave name |
| `bits` | 8 or 16 |
| `signed` | `true` for signed sample data |
| `loop` | `"off"`, `"forward"` or `"pingpong"` |
| `reverse` | the reverse mode flag |
| `rate` | sample rate in Hz |
| `frames` | samples in the wave data |
| `loop_start`, `loop_end` | loop points in samples, including the 1/16 sample fractions (numbers) |
| `root_key`, `low_key`, `high_key` | MIDI note numbers (A4 = 69) of the root, low and high frequencies, rounded to the nearest note; `null` for a zero frequency |
| `balance` | pan position 0–15 |
| `peak` | largest absolute value after converting to signed |
| `crc32` | CRC-32 of the signed sample values packed as 16-bit little-endian (8-bit values are not scaled), 8 lower-case hex digits |

Samples are listed up to the first one that is cut short.

## Layout

- `gf1pat/patch.py` – headers, sample headers, sample data
- `patinfo.py` – command line

## Running

    python3 patinfo.py examples/piano.pat examples/strings.pat
    python3 -m unittest discover -s tests
