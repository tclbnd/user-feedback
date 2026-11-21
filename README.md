# vockit

Inventory tool for the speech and sound-effect assets of a DOS game
collection. `vocinfo.py` reads Creative Voice Files (`.voc`) and prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `version` | file format version, e.g. `1.10`, `1.20` |
| `blocks` | number of data blocks of each block type (terminator excluded) |
| `sample_rate` | Hz of the first sound data, as a number |
| `channels`, `codec` | of the first sound data; codec is one of `pcm_u8`, `pcm_s16`, `alaw`, `ulaw`, `adpcm4`, `adpcm26`, `adpcm2` |
| `duration` | seconds as a fraction: all sound data (PCM, A-law, µ-law and Creative ADPCM) plus silence blocks; repeat blocks are not expanded |
| `peak` | largest absolute sample value of the PCM data in the first sound format (8-bit: 0..128, 16-bit: 0..32768), `null` for other codecs |
| `markers` | marker numbers in file order |
| `texts` | ASCII text blocks in file order |

## Layout

- `vockit/voc.py` – header, blocks and summary
- `vocinfo.py` – command line

## Running

    python3 vocinfo.py examples/narrator.voc examples/engine_loop.voc
    python3 -m unittest discover -s tests
