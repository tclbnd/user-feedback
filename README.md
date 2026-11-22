# hcakit

Asset audit for the game's CRI HCA music and voice files. `hcainfo.py` reads
each file's header and prints one JSON summary, which the build uses to check
loop points, encryption settings and broken files before packing.

## Output

| field | meaning |
|---|---|
| `version` | header version `major.minor` |
| `masked` | the chunk names are obfuscated |
| `header_crc_ok` | the header checksum (CRC-16 over the header) is valid |
| `channels`, `sample_rate` | from the format chunk |
| `blocks`, `block_size` | frames in the file and bytes per frame (`null` for a variable-rate file) |
| `vbr` | the file is variable-rate |
| `encoder_delay`, `padding` | samples muted at the start and at the end |
| `samples`, `duration` | playable samples per channel and their duration in seconds as a fraction string |
| `loop` | `{"start", "end"}` in playable samples (end exclusive), else `null` |
| `ath_type` | absolute threshold of hearing table in use |
| `cipher` | `none`, `keyless` or `keyed` |
| `volume` | relative volume from the header (1.0 when not set) |
| `comment` | header comment, else `null` |
| `truncated` | the file holds fewer frame bytes than `blocks` × `block_size` (`null` for a variable-rate file) |

## Layout

- `hcakit/hca.py` – header chunks
- `hcainfo.py` – command line

## Running

    python3 hcainfo.py examples/bgm_battle.hca examples/vo_intro.hca
    python3 -m unittest discover -s tests
