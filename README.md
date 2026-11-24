# tzxkit

Check for the ZX Spectrum tape images (`.tzx`) in the preservation archive.
`tzxinfo.py` prints one JSON summary per tape, used to check block lists,
playing times and the files on each tape before the images are published.

## Output

| field | meaning |
|---|---|
| `version` | the TZX version as `major.minor` (two-digit minor, e.g. `1.20`) |
| `blocks`, `block_types` | the number of blocks, and blocks counted by ID as `0xNN` (upper-case hex) |
| `files` | per standard-speed header block (19 bytes, flag 0): `type` (`program`, `number_array`, `character_array`, `bytes`, else `type_N`), `name` (trailing spaces removed), `length` and `param` (parameter 1: the autostart line or the start address) |
| `checksum_errors` | standard-speed blocks whose checksum byte does not match |
| `stops` | places where the tape stops (an integer) |
| `archive` | the archive info block as `{name: text}` with `title`, `publisher`, `author`, `year`, `language`, `type`, `price`, `loader`, `origin`, `comment`, else `info_NN`; `null` without one |
| `texts` | the text description blocks in order |
| `duration` | the playing time in seconds as a fraction string: every block's pulses at 3.5 MHz plus its pause, with loops played as often as they say (jumps are not followed) |

Block layouts, the ROM loader's pilot, sync and bit timings, pauses, groups,
loops and the info blocks follow the TZX format 1.20 and the ZX Spectrum ROM
tape routines.

## Layout

- `tzxkit/tzx.py` – blocks, timings, headers
- `tzxinfo.py` – command line

## Running

    python3 tzxinfo.py examples/game.tzx examples/turbo.tzx
    python3 -m unittest discover -s tests
