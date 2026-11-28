# sndkit

Sample check for the SND1 sound effects and speech of Westwood's mid-90s
games. The rip tool stores the audio chunks of one sound as an `.snd` pack;
`sndinfo.py` decodes them the way the game's sound driver does and prints one
JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `SND1PACK` |
| 2 | sample rate (`u16` little-endian) |
| 2 | chunk count (`u16` little-endian) |
| | for each chunk its size (`u32` little-endian) and the chunk as stored in the sound file |

A chunk starts with its output size and input size (`u16` little-endian
each), followed by the coded samples: 2-bit and 4-bit ADPCM, raw bytes, big
deltas and runs. Output samples are 8-bit unsigned (128 is silence).

## Output

| field | meaning |
|---|---|
| `rate` | header value |
| `chunks` | samples produced by each chunk (`null` for a chunk the driver rejects) |
| `samples`, `duration` | total samples and `samples / rate` rounded to 4 decimals |
| `peak`, `sum` | largest absolute value and sum of the samples taken as signed (sample − 128) |
| `crc32` | CRC-32 of the 8-bit samples, lower-case hex |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `sndkit/snd1.py` – chunk decoder
- `sndinfo.py` – command line

## Running

    python3 sndinfo.py examples/speech.snd examples/blast.snd
    python3 -m unittest discover -s tests
