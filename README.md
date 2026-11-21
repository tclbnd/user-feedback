# dtskit

Checker for the surround soundtracks delivered with the film archive.
`dtsinfo.py` reads DTS Coherent Acoustics core streams — raw `.dts` files and
DTS-in-WAV/CD images, where the stream is stored as 14-bit words inside
16-bit PCM — and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `layout` | `BE16`, `LE16` (16-bit words, big/little-endian) or `BE14`, `LE14` (14 bits used in each 16-bit word) |
| `frames` | complete core frames |
| `sample_rate`, `channels`, `lfe`, `bitrate_kbps`, `crc` | from the first frame header (`channels` without the LFE channel; `bitrate_kbps` is `null` for open, variable or lossless rates) |
| `samples_per_frame` | PCM samples per channel in the first frame |
| `extensions` | core extensions announced in the frames (`XCh`, `X96`, `XXCh`), sorted |
| `duration` | total samples of all frames ÷ sample rate, as a fraction string |
| `variable_frame_size` | the frames do not all have the same size |

Bytes before the first frame (a WAV header, for example) and junk between
frames are skipped; a frame cut off at the end of the file is not counted.

## Layout

- `dtskit/dts.py` – sync words, frame header, stream scan
- `dtsinfo.py` – command line

## Running

    python3 dtsinfo.py examples/trailer.dts examples/concert.wav
    python3 -m unittest discover -s tests
