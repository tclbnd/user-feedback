# m2vscan

Ingest check for MPEG video elementary streams delivered by the broadcast
archive. `m2vinfo.py` walks the start codes of a raw stream and prints one
JSON summary per file; the playout scheduler and the transcode ladder read
these fields.

## Output

| field | meaning |
|---|---|
| `standard` | `MPEG-1` or `MPEG-2` |
| `width`, `height` | coded frame size |
| `display_width`, `display_height` | display size (coded size when the stream does not give one) |
| `dar` | display aspect ratio, `w:h` in lowest terms |
| `sar` | sample aspect ratio, `w:h` in lowest terms |
| `frame_rate` | frames per second as a fraction, e.g. `30000/1001` |
| `bit_rate` | bit/s, or `null` when the stream signals a variable rate |
| `vbv_buffer_size` | bits |
| `profile`, `level` | profile and level names, `null` when not signalled |
| `chroma_format` | `4:2:0`, `4:2:2` or `4:4:4` |
| `progressive_sequence` | as signalled |
| `pictures` | number of coded pictures per type (`I`, `P`, `B`, `D`); each field picture counts on its own |
| `frames` | number of coded frames; two field pictures make one frame |
| `gops` | number of GOP headers |
| `duration` | display time in seconds as a fraction |

## Layout

- `m2vkit/bits.py` – bit reader
- `m2vkit/headers.py` – sequence, extension, GOP and picture headers
- `m2vkit/stream.py` – start code walk and summary
- `m2vinfo.py` – command line

## Running

    python3 m2vinfo.py examples/promo_pal.m2v examples/news_ntsc.m2v
    python3 -m unittest discover -s tests
