# cafkit

Catalogue checker for the voice memos and field recordings exported from the
iOS recorder app as Core Audio Format (`.caf`) files. `cafinfo.py` prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `format`, `channels`, `bits` | from the `desc` chunk (`format` without trailing spaces) |
| `bytes_per_packet`, `frames_per_packet` | from `desc` (0 means the value varies per packet) |
| `float`, `little_endian` | for `lpcm` only, from the format flags (`null` for other formats) |
| `chunks` | chunk types in file order |
| `edit_count` | edit count at the start of the `data` chunk |
| `data_bytes` | bytes of audio data (the edit count is not audio) |
| `packets` | packets of audio |
| `frames` | playable frames: priming and remainder frames are not counted |
| `priming`, `remainder` | from the packet table (0 without one) |
| `duration` | `frames` / `sample_rate` in seconds, as a fraction string |
| `max_packet_bytes`, `max_packet_frames` | largest packet in bytes and in frames, for buffer sizing |
| `info` | `info` chunk strings as a key/value object |
| `channel_layout` | `tag` and `bitmap` from the `chan` chunk, or `null` |

## Layout

- `cafkit/caf.py` – chunks, audio description, packet table
- `cafinfo.py` – command line

## Running

    python3 cafinfo.py examples/voice_memo.caf examples/field.caf
    python3 -m unittest discover -s tests
