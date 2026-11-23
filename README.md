# nsvkit

Check for the web-radio and web-TV archive kept as Nullsoft Streaming Video
files (`.nsv`, saved from streams or written by the encoder). `nsvinfo.py`
walks the frames of each file and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `header` | the NSVf file header: `file_size`, `length_ms` (`null` when unknown), `metadata` (name/value pairs), `toc_entries`; `null` for a stream without a file header |
| `video_format`, `audio_format`, `width`, `height`, `fps` | from the first sync frame; `fps` as a fraction string |
| `frames`, `sync_frames` | all frames, and those starting with an NSVs sync header |
| `format_changes` | later sync headers that announce a different format or size |
| `video_bytes`, `audio_bytes` | video and audio payload over all frames (auxiliary data is not video) |
| `aux` | auxiliary chunk counts by type |
| `skipped_bytes` | bytes between frames that do not belong to any frame |
| `duration` | frames divided by `fps`, seconds as a fraction string |

Metadata values may be quoted with `"` or `'` and then contain spaces.

Also `aux_bytes` (total data bytes of all auxiliary chunks, their 6-byte chunk
headers excluded), `largest_video_frame` (video bytes of the largest frame,
auxiliary data excluded) and `audio_frames` (the number of frames that carry audio data).

## Layout

- `nsvkit/nsv.py` – file header, metadata, frames
- `nsvinfo.py` – command line

## Running

    python3 nsvinfo.py examples/session.nsv examples/capture.nsv
    python3 -m unittest discover -s tests
