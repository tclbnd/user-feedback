# rstmkit

Asset audit for the game's streamed music and voice files (Nintendo `.brstm`).
`rstminfo.py` reads each stream's header and prints one JSON summary, which the
build uses to catch broken loops and truncated streams.

## Output

| field | meaning |
|---|---|
| `version` | file format version `major.minor` |
| `codec` | `pcm8`, `pcm16` or `dsp-adpcm` |
| `channels`, `sample_rate`, `total_samples` | from the stream info |
| `duration` | `total_samples` ÷ `sample_rate`, as a fraction string |
| `loop` | `{"start", "start_seconds"}` for a looping stream, else `null` |
| `blocks`, `block_size`, `samples_per_block`, `last_block_samples` | interleave layout |
| `tracks` | per track: `channels` (channel indices), `volume`, `pan` (`null` when the track table does not carry them) |
| `initial_predictor_scale` | DSP-ADPCM only: each channel's initial predictor/scale as `0x..`, else `null` |
| `has_adpc` | the file has an ADPC chunk |
| `data_bytes_expected` | audio bytes the interleave layout needs (every block is padded to 32 bytes per channel in the last block) |
| `truncated` | the DATA chunk holds fewer bytes than `data_bytes_expected` |

## Layout

- `rstmkit/rstm.py` – header and chunks
- `rstminfo.py` – command line

## Running

    python3 rstminfo.py examples/field_theme.brstm examples/menu_click.brstm
    python3 -m unittest discover -s tests
