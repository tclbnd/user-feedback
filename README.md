# modkit

Catalogue tool for the tracker music archive of the retro game collection.
`modinfo.py` reads Amiga tracker modules (`.mod`) and prints one JSON summary
per file; the catalogue page shows the channel count, sample list and running
time.

## Output

| field | meaning |
|---|---|
| `format` | `31-sample` (with a format tag at offset 1080) or `15-sample` (original Soundtracker layout) |
| `signature` | the 4-byte format tag, `null` for 15-sample modules |
| `channels` | number of channels |
| `title` | song title |
| `song_length` | number of positions played from the order table |
| `patterns` | number of patterns stored in the file |
| `samples` | samples with a non-zero length: `index`, `name`, `length` (bytes), `finetune` (-8..7), `volume`, `loop_start` and `loop_length` (bytes), `looped` |
| `sample_bytes` | sum of the sample lengths declared in the header |
| `sample_bytes_present` | sample bytes actually present after the patterns |
| `duration` | seconds (as a fraction) for one play-through following the ProTracker speed/tempo and position jump / pattern break effects; playback stops when it would revisit a row already played or runs past the song length |

## Layout

- `modkit/module.py` – header, order table, patterns
- `modkit/timing.py` – play-through duration
- `modkit/info.py` – summary
- `modinfo.py` – command line

## Running

    python3 modinfo.py examples/harbour.mod examples/old_st.mod
    python3 -m unittest discover -s tests
