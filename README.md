# smkkit

Asset report for the cut-scenes of the game remaster, stored as RAD Smacker
videos (`.smk`). `smkinfo.py` reads each file and prints one JSON summary,
used to plan the re-encode of every video and its audio tracks.

## Output

| field | meaning |
|---|---|
| `version` | `SMK2` or `SMK4` |
| `width`, `height` | picture size as displayed (interlaced and line-doubled videos show twice the stored height) |
| `interlaced` | the video is interlaced |
| `frames` | frames of the video, not counting the ring frame |
| `ring_frame` | the file has a ring frame (for looping) |
| `fps`, `duration` | frames per second and playing time in seconds, as fraction strings |
| `keyframes`, `palette_frames` | frames flagged as keyframes and frames carrying a palette, ring frame excluded |
| `video_bytes` | bytes of video data, ring frame excluded |
| `audio` | per audio track that carries data: `track` (0–6), `sample_rate`, `bits`, `channels`, `codec` (`pcm`, `smacker`, `bink-rdft`, `bink-dct`), `bytes` of audio data (ring frame excluded) |

A frame that runs past the end of the file is an error.

## Layout

- `smkkit/smk.py` – header, frame tables, chunks
- `smkinfo.py` – command line

## Running

    python3 smkinfo.py examples/intro.smk examples/ending.smk
    python3 -m unittest discover -s tests
