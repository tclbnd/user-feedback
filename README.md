# fsbkit

Asset audit for the game's FMOD sound banks (`.fsb`, FSB5). `fsbinfo.py`
reads each bank and prints one JSON summary per bank with one entry per sample,
used to check sample rates, loops and memory budgets before shipping.

## Output

| field | meaning |
|---|---|
| `version` | FSB5 sub-version (0 or 1) |
| `codec` | `pcm8`, `pcm16`, `pcm24`, `pcm32`, `pcmfloat`, `gcadpcm`, `imaadpcm`, `vag`, `hevag`, `xma`, `mpeg`, `celt`, `at9`, `xwma`, `vorbis`, `fadpcm`, `opus` |
| `sample_count`, `data_bytes` | samples in the bank and size of the sample data section |
| `truncated` | the file holds less sample data than the header says |
| `samples` | per sample: `name` (`null` without a name table), `sample_rate`, `channels`, `samples` (per channel), `duration` (seconds, fraction string), `loop` (`{"start", "end"}` in samples as stored, else `null`), `extras` (types of the extra chunks in file order), `data_bytes` (bytes of this sample's data) |

A sample's rate and channel count come from its header unless an extra chunk
gives them explicitly.

## Layout

- `fsbkit/fsb.py` – header, sample headers, name table
- `fsbinfo.py` – command line

## Running

    python3 fsbinfo.py examples/music.fsb examples/ui.fsb
    python3 -m unittest discover -s tests
