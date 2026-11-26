# vagkit

Check for the VAG sound effects (`.vag`) the audio team extracts from
PlayStation discs. `vaginfo.py` prints one JSON summary per file, so a
re-recorded effect can be compared with what the SPU plays.

## Input

VAG files exactly as the PlayStation tools write them: the 48-byte `VAGp`
header (version, data size, sample rate, name), then SPU ADPCM data in 16-byte
frames of 28 samples each, including the frames' loop flags.

## Output

| field | meaning |
|---|---|
| `name`, `version`, `sample_rate` | from the header |
| `frames` | whole frames in the data the header announces |
| `samples` | samples the SPU plays before it stops or loops back (whole frames) |
| `duration` | `samples / sample_rate` as an exact fraction string |
| `ended` | `true` when a frame marks the end of the sound |
| `looped` | `true` when the sound loops back instead of stopping there |
| `loop_start` | sample index where a looping sound restarts, `null` otherwise |
| `peak` | largest absolute decoded sample / 32768, six decimals |
| `last_sample` | last decoded sample of the played part |

Decoding starts with both history samples at 0; the filter's prediction is
rounded as `(…  + 32) >> 6`, and decoded samples are clamped to 16 bits.

## Layout

- `vagkit/vag.py` – header, frames, decoding
- `vaginfo.py` – command line

## Running

    python3 vaginfo.py examples/jump.vag examples/engine.vag
    python3 -m unittest discover -s tests
