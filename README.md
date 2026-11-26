# xakit

Check for the CD-XA audio streams (`.xa`) the audio team rips from
PlayStation and CD-i discs. `xainfo.py` prints one JSON summary per file with
every interleaved stream's format, length and level, so re-recorded music and
voice can be compared with what the drive decodes.

## Input

Raw Mode 2 Form 2 sectors of 2336 bytes as the ripping tool saves them: the
8-byte subheader (file number, channel, submode, coding information, each
written twice), then the sector data. Audio sectors carry 18 sound groups of
128 bytes in 4-bit or 8-bit ADPCM, mono or stereo, at 37800 or 18900 Hz.
Several streams can be interleaved, and data sectors can sit between them.

## Output

| field | meaning |
|---|---|
| `sectors` | whole sectors in the file |
| `complete` | `true` when the file is a whole number of sectors |
| `streams` | one object per audio stream (file number / channel), in order of first appearance |

Each stream: `stream` (`"file/channel"`), `sectors`, `stereo`, `sample_rate`,
`bits`, `samples` (decoded samples per channel), `duration` (`samples /
sample_rate` as an exact fraction), `peak` (largest absolute decoded sample /
32768, six decimals), `last_left` and `last_right` (last decoded sample of
each channel, `last_right` `null` for mono). Decoding starts with all history
samples at 0, the filter's prediction is rounded as `(… + 32) >> 6`, and
samples are clamped to 16 bits.

## Layout

- `xakit/xa.py` – sectors, sound groups, decoding
- `xainfo.py` – command line

## Running

    python3 xainfo.py examples/music.xa examples/voices.xa
    python3 -m unittest discover -s tests
