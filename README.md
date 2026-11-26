# brrkit

Check for the SNES BRR samples (`.brr`) the audio team rips from cartridges
while re-recording instruments and effects. `brrinfo.py` prints one JSON
summary per file, so a re-recorded sample can be compared with what the
S-DSP plays.

## Input

BRR files as the ripping tools save them: 9-byte BRR blocks, optionally
preceded by the 2-byte little-endian loop offset the tools store in front of
looped samples.

## Output

| field | meaning |
|---|---|
| `blocks` | BRR blocks in the file |
| `loop_header` | `true` when the file starts with a loop offset |
| `samples` | samples the S-DSP plays before it stops or loops back (whole blocks) |
| `ended` | `true` when a block marks the end of the sample |
| `looped` | `true` when the sample loops back instead of stopping there |
| `loop_start` | sample index where a looping sample restarts, `null` otherwise |
| `peak` | largest absolute decoded sample / 32768, six decimals |
| `last_sample` | last decoded sample of the played part |

Decoded samples are the 16-bit values the S-DSP keeps in its sample buffer;
decoding starts with both history samples at 0.

## Layout

- `brrkit/brr.py` – blocks, decoding
- `brrinfo.py` – command line

## Running

    python3 brrinfo.py examples/strings.brr examples/kick.brr
    python3 -m unittest discover -s tests
