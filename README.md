# nsfkit

Catalogue checker for the NES music rips in the chiptune archive (`.nsf`,
`.nsfe`). `nsfinfo.py` reads each file's header or chunks and prints one JSON
summary.

## Output

| field | meaning |
|---|---|
| `format`, `version` | `NSF` (with its header version) or `NSFe` (`version` is `null`) |
| `songs`, `start_song` | number of songs and the song that plays first, counted from 1 |
| `title`, `artist`, `copyright` | text fields, `null` when the file has none |
| `region` | `NTSC`, `PAL` or `dual` |
| `play_rate_hz` | calls of the play routine per second for the region the file is meant for, as a fraction string (dual files use the NTSC rate) |
| `chips` | expansion sound chips: `VRC6`, `VRC7`, `FDS`, `MMC5`, `Namco163`, `Sunsoft5B`, `VT02` |
| `bankswitched` | the file uses bank switching |
| `load`, `init`, `play` | addresses as 4 hex digits |
| `track_labels`, `track_times_ms`, `playlist` | NSFe only: one label per song, song lengths in milliseconds (`null` for an unknown length), and the playlist as song numbers counted from 1; `null` when absent |
| `data_bytes` | bytes of program data |

An NSFe chunk this tool does not know is skipped when its name starts with a
lower-case letter and is an error when it starts with an upper-case letter.

## Layout

- `nsfkit/nsf.py` – NSF header, NSFe chunks
- `nsfinfo.py` – command line

## Running

    python3 nsfinfo.py examples/castle.nsf examples/racing.nsfe
    python3 -m unittest discover -s tests
