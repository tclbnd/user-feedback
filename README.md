# roqkit

Inventory of the RoQ cut-scene movies (`.roq`) from the studio's id Tech
licensed titles, ahead of re-encoding. `roqinfo.py` reads each movie and prints
one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `fps` | frame rate from the file header (an integer) |
| `width`, `height` | from the info chunk, `null` without one |
| `frames` | the number of video frames (vector-quantised and JPEG frames) |
| `jpeg_frames` | the number of those that are JPEG frames |
| `codebooks` | the number of codebook chunks |
| `cells_2x2`, `cells_4x4` | the numbers of 2×2 and 4×4 cells the codebook chunks define, added up |
| `audio_channels` | 1 or 2, from the first audio chunk, `null` without audio |
| `audio_samples` | the number of audio sample frames (per channel) in all audio chunks |
| `audio_predictors` | initial predictors of the first audio chunk as signed 16-bit integers: `[value]` for mono, `[left, right]` for stereo; `null` without audio |
| `video_duration` | `frames / fps` in seconds as a fraction string, `null` when `fps` is 0 |
| `audio_duration` | `audio_samples / 22050` in seconds as a fraction string |
| `packets` | the number of packet chunks (id 0x1030) |
| `unknown_chunks` | the number of chunks with any other id |
| `truncated` | `true` when the last chunk runs past the end of the file |

Chunk headers, codebook counts and audio chunks follow the RoQ format as id
Software's engines read it.

## Layout

- `roqkit/roq.py` – chunks, codebooks, frames, audio
- `roqinfo.py` – command line

## Running

    python3 roqinfo.py examples/intro.roq examples/briefing.roq
    python3 -m unittest discover -s tests
