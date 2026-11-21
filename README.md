# adpcmkit

Decoder for ADPCM WAVE files coming from voice recorders, intercoms and old
game assets. `adpcminfo.py` decodes each file and prints one JSON summary; the
archive keeps the decoded PCM checksum to detect regressions.

Supported formats: IMA ADPCM (`WAVE_FORMAT_IMA_ADPCM`, 0x0011, 4-bit) and
Microsoft ADPCM (`WAVE_FORMAT_ADPCM`, 0x0002).

## Output

| field | meaning |
|---|---|
| `format` | `ima_adpcm` or `ms_adpcm` |
| `channels`, `sample_rate`, `block_align` | from the fmt chunk |
| `samples_per_block` | decoded samples per channel in a full block |
| `blocks` | number of blocks decoded (a trailing partial block counts) |
| `frames` | decoded samples per channel |
| `duration` | `frames / sample_rate`, seconds as a fraction |
| `peak` | per channel, largest absolute decoded sample |
| `head` | per channel, the first 4 decoded samples in output order |
| `pcm_crc32` | CRC-32 (hex) of the decoded PCM as 16-bit little-endian, channels interleaved |

## Layout

- `adpcmkit/wav.py` – RIFF walk and fmt chunk
- `adpcmkit/tables.py` – step, index, adaptation and coefficient tables
- `adpcmkit/codec.py` – block decoders
- `adpcmkit/info.py` – decode and summary
- `adpcminfo.py` – command line

## Running

    python3 adpcminfo.py examples/intercom_ima.wav examples/game_sfx_ms.wav
    python3 -m unittest discover -s tests
