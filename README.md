# rstmkit

Inventory of the streamed music (`.brstm`) ripped from the studio's Wii titles
for the soundtrack re-release. `rstminfo.py` reads each stream and prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `version` | header version as `major.minor` |
| `byte_order` | `big` or `little`, from the byte order mark |
| `codec` | `pcm8`, `pcm16`, `dsp_adpcm`, else `codec_N` |
| `channels` | channel count from the stream information |
| `listed_channels` | the number of channels the channel information lists |
| `sample_rate` | sample rate in Hz (an integer) |
| `loop` | `true` when the stream loops |
| `loop_start` | loop start in samples (an integer), `null` when the stream does not loop |
| `total_samples` | samples per channel (an integer) |
| `duration` | `total_samples / sample_rate` in seconds as a fraction string |
| `blocks`, `block_samples`, `last_block_samples` | from the stream information (integers) |
| `tracks` | the number of tracks the track information lists |
| `track_info` | `short` or `long`, the layout of the track entries |
| `data_bytes` | the number of bytes of audio in the DATA chunk |
| `expected_data_bytes` | the number of bytes the block layout implies: every block but the last at the full block size, the last at its padded size, times the channel count |
| `truncated` | `true` when the DATA chunk runs past the end of the file |
| `seek_interval` | samples per ADPC seek-table entry, from the stream information (an integer) |
| `adpcm` | for `dsp_adpcm`: one entry per listed channel with `coef` (the first two of its 16 coefficients, signed integers), `gain`, `ps` (initial predictor/scale), `hist` (`[hist1, hist2]`, signed), `loop_ps` and `loop_hist` (signed), all integers from the channel's ADPCM information; `null` for other codecs |

Header, chunk and information layouts follow the BRSTM format as the Wii SDK
writes it.

## Layout

- `rstmkit/rstm.py` – header, HEAD chunk, information blocks
- `rstminfo.py` – command line

## Running

    python3 rstminfo.py examples/title_theme.brstm examples/menu_loop.brstm
    python3 -m unittest discover -s tests
