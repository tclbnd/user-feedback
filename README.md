# thpkit

Inventory of the cut-scene movies (`.thp`) pulled from the studio's GameCube and
Wii back catalogue before remastering. `thpinfo.py` reads each movie and prints
one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `version` | `major.minor` from the header version word (`1.0`, `1.1`) |
| `fps` | frame rate from the header, `%.3f` |
| `frames` | the number of frames the header gives (an integer) |
| `duration` | `frames / fps` in seconds, `%.3f`, `null` when the rate is not positive |
| `video` | `{"width", "height"}` of the video component, `null` without one |
| `audio` | `{"channels", "sample_rate", "samples"}` of the audio component (the header's sample count), `null` without one |
| `frames_walked` | the number of frames read by following the frame chain from the first frame |
| `video_bytes` | the number of bytes of video data in the frames walked |
| `audio_samples` | the number of audio samples (per channel) the audio data of the frames walked holds |
| `largest_frame` | size in bytes of the largest frame walked |
| `truncated` | `true` when the chain runs past the end of the file before the header's frame count is reached |

THP files are big-endian. Components are listed in the order the component
table gives them. Each frame starts with the size of the next frame, the size
of the previous frame and one data size per component, in component order.

## Layout

- `thpkit/thp.py` – header, components, frames
- `thpinfo.py` – command line

## Running

    python3 thpinfo.py examples/opening.thp examples/credits.thp
    python3 -m unittest discover -s tests
