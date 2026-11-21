# mp4kit

Sample-table inspector for the clip ingest service (MP4 / MOV from cameras,
phones and the transcoder). `mp4info.py` prints one JSON summary per file.

## Output

Movie: `timescale`, `duration` (seconds as a fraction string) from the movie header, and `tracks`.

Per track:

| field | meaning |
|---|---|
| `id`, `handler`, `enabled` | track ID, handler type, track-enabled flag |
| `timescale`, `media_duration` | media timescale; media duration in seconds (sum of sample deltas) |
| `samples`, `chunks`, `bytes` | sample count, chunk count, total sample bytes |
| `first_offset`, `last_offset` | file offsets of the first and the last sample |
| `sync_samples` | number of sync samples |
| `ctts_min`, `ctts_max` | smallest and largest composition offset (`null` without ctts) |
| `edit_delay` | total duration of empty edits, seconds |
| `edit_media_start` | media time of the first non-empty edit, media timescale units (`null` without one) |
| `presentation_duration` | sum of all edit durations in seconds; `media_duration` when there is no edit list |

## Layout

- `mp4kit/boxes.py` – box walking
- `mp4kit/tracks.py` – sample tables, edit lists
- `mp4info.py` – command line

## Running

    python3 mp4info.py examples/camera.mp4 examples/phone.mov
    python3 -m unittest discover -s tests
