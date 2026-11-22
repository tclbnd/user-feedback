# dvkit

Digitisation report for the DV tapes captured to raw DIF streams (`.dv`).
`dvinfo.py` reads each capture and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `system`, `frame_rate` | `525-60` (30000/1001) or `625-50` (25) |
| `bitrate_mbps` | 25 for DV25, 50 for DV50 (two DIF channels per frame) |
| `frames`, `duration` | complete frames in the file; `duration` in seconds as a fraction string |
| `trailing_bytes` | bytes after the last complete frame (an interrupted capture) |
| `aspect` | display aspect from the video source control pack: `4:3`, `14:9`, `16:9`, or `null` |
| `audio` | from the AAUX source pack of the first frame: `sample_rate`, `bits`, `channels`, `locked`; `null` without audio |
| `audio_samples` | audio samples per channel in the whole file, from each frame's AAUX source pack |
| `first_timecode`, `last_timecode` | subcode timecode `HH:MM:SS:FF`; drop-frame timecode uses `;` before the frames |
| `recorded` | recording date and time of the first frame, `YYYY-MM-DDTHH:MM:SS` |

## Layout

- `dvkit/dv.py` – DIF blocks, packs, timecode
- `dvinfo.py` – command line

## Running

    python3 dvinfo.py examples/wedding_ntsc.dv examples/interview_pal.dv
    python3 -m unittest discover -s tests
