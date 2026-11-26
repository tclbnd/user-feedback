# adwav

Check for the Microsoft ADPCM WAV files (format tag 2) in the game's sound
effect library. `adinfo.py` decodes each file to 16-bit PCM and prints one
JSON summary per file.

## Decoding notes

Files follow the Microsoft ADPCM layout of the RIFF WAVE format. The
prediction `(sample1 × coef1 + sample2 × coef2)` is divided by 256 with an
arithmetic shift (rounding towards minus infinity). Decoded samples are
clamped to 16 bits. A last block shorter than the block align decodes the
samples it holds.

## Output

| field | meaning |
|---|---|
| `channels`, `sample_rate`, `block_align`, `samples_per_block` | from the fmt chunk |
| `blocks` | blocks in the data chunk (a short last block counts) |
| `frames` | decoded sample frames |
| `duration` | `frames / sample_rate` in seconds, rounded to 4 places |
| `peak` | largest absolute sample value over all channels |
| `head` | per channel, the first 6 decoded samples in playback order |
| `last` | per channel, the last decoded sample |
| `crc32` | CRC-32 of the decoded PCM, interleaved 16-bit little-endian, as 8 lower-case hex digits |

## Layout

- `adwav/msadpcm.py` – RIFF chunks, fmt, block decoding
- `adinfo.py` – command line

## Running

    python3 adinfo.py examples/door.wav examples/rain.wav
    python3 -m unittest discover -s tests
