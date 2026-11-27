# eakit

Check for the Electronic Arts ADPCM audio the team pulls out of EA game
streams (the `SCDl` data chunks of `.asf`/`.str` files). The extraction tool
stores the payload of each data chunk of one stream as a packet stream
(`.eas`); `eainfo.py` decodes the packets in order and prints one JSON
summary per file.

## File layout

`EAADPCM` and a NUL, the channel count (1 or 2), a pad byte, the sample rate
(`u32` little-endian), the number of packets (`u16` little-endian), then for
each packet its length (`u32` little-endian) and the chunk payload exactly as
stored, starting with its sample count.

A packet that cannot be decoded is reported with `ok` false and adds no
samples.

## Output

| field | meaning |
|---|---|
| `channels`, `rate` | from the stream header |
| `packets` | one object per packet: `ok` and `samples` (per channel) |
| `samples`, `duration` | decoded samples per channel and seconds (four decimals) |
| `peak`, `crc32` | largest absolute value and CRC-32 of the decoded audio as interleaved signed 16-bit little-endian samples, lower-case hex |

## Layout

- `eakit/adpcm.py` – packet decoder
- `eainfo.py` – command line

## Running

    python3 eainfo.py examples/music.eas examples/voice.eas
    python3 -m unittest discover -s tests
