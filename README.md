# ttakit

Check for the True Audio (`.tta`) masters in the music archive. `ttainfo.py`
prints one JSON summary per file, used to catch damaged rips before they are
transcoded for the store.

## Output

| field | meaning |
|---|---|
| `id3v2_bytes` | bytes taken by an ID3v2 tag in front of the TTA data (0 without one) |
| `format` | `pcm`, `pcm_encrypted` or `float` |
| `channels`, `bits`, `sample_rate` | from the TTA header (integers) |
| `samples` | samples per channel (an integer) |
| `duration` | length in seconds as a fraction string |
| `frame_samples` | samples per channel in every frame but the last |
| `frames` | the number of frames (seek table entries) |
| `last_frame_samples` | samples per channel in the last frame (0 without audio) |
| `header_crc_ok`, `seek_table_crc_ok` | whether the header and the seek table match their CRC32 |
| `audio_bytes` | total compressed bytes the seek table lists (`null` when the table is cut off) |
| `complete` | `true` when the file holds every frame the seek table lists |

The header, the seek table and its CRC, the frame length and a leading ID3v2
tag follow the TTA1 format and ID3v2.4.

## Layout

- `ttakit/tta.py` – header, seek table, frames
- `ttainfo.py` – command line

## Running

    python3 ttainfo.py examples/live.tta examples/voice.tta
    python3 -m unittest discover -s tests
