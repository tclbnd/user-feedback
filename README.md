# dk3kit

Sample check for the Duck DK3 ADPCM soundtracks of mid-90s FMV games (the
TrueMotion movies on Sega Saturn and PC). The rip tool stores the audio
stream of one movie as a `.dk3` file; `dk3info.py` decodes it the way the
games' player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `DK3SOUND` |
| 4 | sample rate (`u32` little-endian) |
| 2 | channel count (`u16` little-endian) |
| 2 | block size in bytes (`u16` little-endian) |
| | the stream, cut into blocks of that size (the last one may be shorter) |

Each block is one Duck DK3 ADPCM block as stored in the movie: a 16-byte
block header followed by the coded samples.

## Output

| field | meaning |
|---|---|
| `rate`, `channels`, `block_size` | header values |
| `blocks` | blocks that produced samples |
| `frames` | stereo sample frames decoded |
| `duration` | `frames / rate`, rounded to 4 decimals |
| `left`, `right` | per channel: `samples`, `peak` (largest absolute value), `sum` and `crc32` (of the samples as signed 16-bit little-endian, lower-case hex) |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `dk3kit/dk3.py` – block decoder
- `dk3info.py` – command line

## Running

    python3 dk3info.py examples/intro.dk3 examples/race.dk3
    python3 -m unittest discover -s tests
