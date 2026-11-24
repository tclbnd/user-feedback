# adxkit

Inventory of the CRI ADX voice and music files (`.adx`) from the studio's
console back catalogue. `adxinfo.py` reads each file and prints one JSON
summary per file.

## Output

| field | meaning |
|---|---|
| `encoding` | `adx_preset`, `adx_standard`, `adx_exponential`, `ahx`, else `type_N` |
| `block_size`, `bit_depth`, `channels` | from the header (integers) |
| `sample_rate` | in Hz (an integer) |
| `total_samples` | samples per channel the header gives (an integer) |
| `duration` | `total_samples / sample_rate` in seconds as a fraction string |
| `highpass` | high-pass cutoff in Hz (an integer) |
| `version` | header version (an integer) |
| `encrypted` | `true` when the header marks the audio as encrypted |
| `copyright_ok` | `true` when the `(c)CRI` signature sits where the header's copyright offset points |
| `data_offset` | file offset of the first audio frame |
| `loop` | `true` when the header's loop information is present and enabled |
| `loop_start`, `loop_end` | loop points in samples, `null` without a loop |
| `loop_start_byte`, `loop_end_byte` | loop points as byte offsets, `null` without a loop |
| `samples_per_block` | samples one block of one channel holds |
| `data_frames` | the number of whole frames (one block per channel) before the end-of-stream frame or the end of the file |
| `data_samples` | `data_frames × samples_per_block` |

Header fields, loop information and the frame layout follow the ADX format as
CRI's libraries write it for each header version.

## Layout

- `adxkit/adx.py` – header, loop information, frames
- `adxinfo.py` – command line

## Running

    python3 adxinfo.py examples/bgm_stage1.adx examples/voice_0042.adx
    python3 -m unittest discover -s tests
