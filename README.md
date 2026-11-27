# schlkit

Check for the streamed audio in Electronic Arts games of the 1990s (music,
speech and effects in `.asf`, `.sng` and `.uv` files). `schlinfo.py` decodes
each stream and prints one JSON summary per file.

## File layout

A stream is a run of chunks. Every chunk is a four-byte tag and a size
(`u32` little-endian), followed by the payload.

| tag | payload |
|---|---|
| `SCHl` | stream header, always the first chunk: channel count (1 or 2), codec (0 = 16-bit PCM, 1 = 8-bit PCM, 2 = EA ADPCM), sample rate (`u32` little-endian) |
| `SCCl` | number of data chunks that follow (`u32` little-endian) |
| `SCDl` | one packet of audio |
| `SCEl` | end of the stream |

Real files carry other chunk types too (loop points, patch data); they carry
no audio.

PCM packets hold interleaved samples: 16-bit signed little-endian, or 8-bit
signed. EA ADPCM is the codec of the original SCHl streams: always stereo,
a 12-byte packet header and then 30-byte pieces of 28 sample frames each.
A packet that cannot be decoded counts 0 samples and the next packet goes on.

## Output

| field | meaning |
|---|---|
| `codec`, `channels`, `rate` | stream parameters; `codec` is `pcm16`, `pcm8` or `ea-adpcm` |
| `packets` | samples (all channels together) each data chunk produced |
| `frames`, `duration` | sample frames over all packets and their length in seconds (four decimals) |
| `channel` | one object per channel: `samples`, `peak` (largest absolute value) and `sum` of the samples, and `crc32` of the channel's samples as 16-bit signed little-endian, lower-case hex; 8-bit PCM samples are scaled to 16 bits (value × 256) first |

## Layout

- `schlkit/stream.py` – chunk reader and decoders
- `schlinfo.py` – command line

## Running

    python3 schlinfo.py examples/music.asf examples/voice.asf examples/sfx.asf
    python3 -m unittest discover -s tests
