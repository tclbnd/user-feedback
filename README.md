# cafscan

Intake check for Core Audio Format recordings sent in from field recorders
and editing workstations. `cafinfo.py` prints one JSON summary per file; the
archive catalogue reads these fields.

## Output

| field | meaning |
|---|---|
| `format_id` | four-character format, e.g. `lpcm`, `aac ` |
| `sample_rate` | Hz, as a number |
| `channels`, `bits` | from the desc chunk |
| `bytes_per_packet`, `frames_per_packet` | from the desc chunk (0 means variable) |
| `packets` | number of packets in the audio data |
| `frames` | number of valid sample frames (what a player renders) |
| `duration` | `frames / sample_rate`, seconds rounded to 6 places |
| `peak` | linear PCM only: per channel, largest absolute sample relative to full scale (0 to 1), rounded to 6 places; `null` for other formats |
| `layout_channels` | number of channels described by the chan chunk, `null` without one |
| `info` | key/value strings from the info chunk |

## Layout

- `caftool/chunks.py` – file header and chunk walk
- `caftool/fields.py` – desc, pakt, chan, info
- `caftool/pcm.py` – sample statistics
- `caftool/info.py` – summary
- `cafinfo.py` – command line

## Running

    python3 cafinfo.py examples/field_take.caf examples/voice_memo.caf
    python3 -m unittest discover -s tests
