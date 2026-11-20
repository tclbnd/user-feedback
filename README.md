# aiffscan

Intake check for AIFF and AIFF-C stems delivered by music post houses.
`aiffinfo.py` prints one JSON summary per file; the session importer reads
these fields to place stems and cue markers on the timeline.

## Output

| field | meaning |
|---|---|
| `format` | `AIFF` or `AIFF-C` |
| `channels`, `bits` | as declared in COMM |
| `sample_rate` | Hz, as a number (may have a fractional part) |
| `compression`, `compression_name` | AIFF-C compression type and name; `NONE` and `null` for plain AIFF |
| `frames` | sample frames declared in COMM |
| `frames_in_file` | complete sample frames actually present in the sound data |
| `duration` | `frames / sample_rate`, seconds rounded to 6 places |
| `peak` | per channel, largest absolute sample relative to full scale (0 to 1), rounded to 6 places, over the frames present |
| `markers` | `id`, `name`, `position` (sample frame) and `seconds` of each marker, in file order |

## Layout

- `aifftool/chunks.py` – FORM and chunk walk
- `aifftool/fields.py` – COMM, MARK and helpers
- `aifftool/samples.py` – sample decoding and peaks
- `aifftool/info.py` – summary
- `aiffinfo.py` – command line

## Running

    python3 aiffinfo.py examples/stem_vox.aif examples/stem_fx.aifc
    python3 -m unittest discover -s tests
