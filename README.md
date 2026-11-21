# mp3kit

Catalogue checker for the podcast and audiobook archive. `mp3info.py` reads
MPEG audio files and prints one JSON summary per file, which the player uses
for duration display and gapless playback.

## Output

| field | meaning |
|---|---|
| `id3v2_bytes` | bytes of ID3v2 tags in front of the first frame (0 if none) |
| `version`, `layer` | MPEG version (`1`, `2`, `2.5`) and layer (1–3) of the first frame |
| `sample_rate`, `channel_mode`, `crc` | from the first frame header |
| `vbr_header` | `Xing`, `Info` or `VBRI` when the first frame is an encoder info frame, else `null` |
| `bitrate_mode` | `VBR` for a `Xing` or `VBRI` info frame, otherwise `CBR` |
| `header_frames` | frame count stored in the info frame (`null` if absent) |
| `frames` | audio frames in the file (an info frame is not audio) |
| `audio_bytes` | bytes of those audio frames |
| `duration` | `frames` × samples per frame ÷ sample rate, as a fraction string |
| `encoder_delay`, `encoder_padding` | samples the encoder added at the start and end (`null` if not recorded) |
| `gapless_samples` | `frames` × samples per frame − delay − padding, when both are known |

Tags at the end of a file (ID3v1, APE) are not audio.

## Layout

- `mp3kit/mp3.py` – frame headers, info frames, file scan
- `mp3info.py` – command line

## Running

    python3 mp3info.py examples/audiobook.mp3 examples/episode.mp3
    python3 -m unittest discover -s tests
