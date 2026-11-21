# psscan

Ingest check for MPEG program streams from the disc archive (VCD `.mpg`,
DVD `.vob`). `psinfo.py` walks the packs and PES packets of a file and prints
one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `format` | `MPEG-1` or `MPEG-2` (system layer of the first pack) |
| `packs` | number of pack headers |
| `mux_rate` | program mux rate of the first pack in bytes per second |
| `first_scr` | SCR of the first pack in seconds, as a fraction |
| `scr_span` | last SCR minus first SCR, seconds as a fraction |
| `padding_bytes` | bytes carried in padding stream packets (not listed under `streams`) |
| `streams` | one entry per elementary stream, see below |

Stream keys are the stream_id in hex (`0xe0`, `0xc0`, ...). For
private_stream_1 the DVD sub-stream number is part of the key
(`0xbd:0x80`). Each entry has:

| field | meaning |
|---|---|
| `kind` | `video`, `audio`, `ac3`, `dts`, `lpcm`, `subpicture`, `private1`, `private2` |
| `packets` | PES packets of this stream |
| `payload_bytes` | elementary stream bytes (PES headers and DVD sub-stream headers excluded) |
| `first_pts`, `last_pts` | PTS in seconds as fractions, `null` when the stream has none |
| `bits`, `sample_rate`, `channels` | LPCM only, from the sub-stream header |

## Layout

- `psscan/headers.py` – pack header, PES header, timestamps
- `psscan/scan.py` – stream walk and summary
- `psinfo.py` – command line

## Running

    python3 psinfo.py examples/vcd_track.mpg examples/dvd_title.vob
    python3 -m unittest discover -s tests
