# qoakit

Asset checker for the game's sound effects and music stored as QOA (Quite OK
Audio). `qoainfo.py` decodes each file and prints one JSON summary, which the
build uses to catch broken or mislabelled assets.

## Output

| field | meaning |
|---|---|
| `streaming` | the file header gives 0 samples (a streamed file) |
| `channels`, `sample_rate` | from the first frame header |
| `samples` | samples per channel: from the file header, or for a streamed file the sum over all frames |
| `frames`, `decoded_samples` | frames in the file and samples per channel they decode to |
| `duration` | `samples` ÷ `sample_rate`, as a fraction string |
| `peak` | largest absolute decoded sample value |
| `pcm_crc32` | CRC-32 of the decoded audio as interleaved 16-bit little-endian PCM, 8 hex digits |

Decoding follows the QOA specification (qoaformat.org) exactly, so the output
matches the reference decoder bit for bit. A frame whose size field does not
match its contents is an error, and so is a change of channels or sample rate
in a file that is not streamed.

## Layout

- `qoakit/qoa.py` – frames, slices, LMS prediction
- `qoainfo.py` – command line

## Running

    python3 qoainfo.py examples/theme.qoa examples/radio.qoa
    python3 -m unittest discover -s tests
