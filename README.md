# okikit

Check for the OKI MSM6295 sample ROMs (`.oki`) the audio team dumps from
arcade boards while re-recording sound effects and voices. `okiinfo.py`
prints one JSON summary per ROM with every phrase the chip can play.

## Input

The ROM image exactly as dumped from the board: the MSM6295 phrase table at
the start, then the ADPCM data of the phrases, up to 256 KB.

## Output

| field | meaning |
|---|---|
| `size` | ROM size in bytes |
| `phrases` | number of phrases listed |
| `phrase_list` | one object per phrase the table defines, by phrase number |

Each phrase: `phrase` (number), `start` and `end` (addresses from the table,
`"$XXXXX"`), `bytes`, `samples` (decoded samples), `peak` (largest absolute
sample / 2048, six decimals), `last_sample`, `valid` (`false` when the table
entry cannot be played from this ROM, in which case the sample fields are
`null`). Each phrase is decoded from the chip's reset state.

## Layout

- `okikit/rom.py` – phrase table, decoding
- `okiinfo.py` – command line

## Running

    python3 okiinfo.py examples/fighter.oki examples/shooter.oki
    python3 -m unittest discover -s tests
