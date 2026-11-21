# w64kit

Ingest check for long location recordings delivered as Sony Wave64 (`.w64`)
files. `w64info.py` prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `format` | `pcm` or `float` |
| `channels`, `sample_rate` | from the format chunk |
| `bits` | significant bits per sample (valid bits for extensible formats) |
| `container_bits` | bits each sample occupies in the data |
| `block_align` | bytes per frame |
| `frames` | sample frames in the data chunk |
| `duration` | seconds as a fraction |
| `peak` | per channel, largest absolute sample relative to full scale (0 to 1), rounded to 6 places |
| `chunks` | chunk names in file order: `fmt`, `data`, `fact`, `list`, `levl`, `junk`, or `unknown` |

## Layout

- `w64kit/w64.py` – chunk walk, format chunk, samples and summary
- `w64info.py` – command line

## Running

    python3 w64info.py examples/field_stereo.w64 examples/float_mono.w64
    python3 -m unittest discover -s tests
