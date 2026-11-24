# aykit

Catalogue check for the ZX Spectrum and Amstrad AY music collection (`.ay`
files in the ZXAYEMUL format). `ayinfo.py` prints one JSON summary per file,
used to check song lists, lengths and the player setup before the tunes go
into the web player.

## Output

| field | meaning |
|---|---|
| `file_version`, `player_version` | from the header (integers) |
| `author`, `misc` | the header strings, `null` when missing or empty |
| `songs`, `first_song` | the number of songs, and the first song's index as stored |
| `song_list` | per song: `name`, `channels` (the AY channel mapping of A, B, C and noise), `length_seconds` and `fade_seconds` (fraction strings; `length_seconds` is `null` when the stored length means the song length is unknown), `hi_reg`, `lo_reg` (integers), `stack`, `init`, `interrupt` (`$XXXX`, `null` without player points; `init` is the address the player actually calls first), `blocks` (memory blocks loaded) and `block_bytes` (bytes those blocks load) |

Pointers, the header, the song structures, song data, player points and the
memory block table (blocks are loaded only as far as both the Z80 address
space and the file allow) follow the ZXAYEMUL (AY) file format.

Notes on the values:

- `songs` is the stored count plus one: the field holds the number of songs
  minus one.
- Song and fade lengths are stored in 1/50 s units and are reported as exact
  fractions of a second.
- All multi-byte fields (relative pointers, stack, init and interrupt
  addresses, block addresses and lengths) are big-endian.
- `init` is the init address when it is set, otherwise the interrupt address,
  since that is what the player calls first; `interrupt` always reports the
  stored value.
- Songs without player points and songs that share a code block with another
  song are both handled: the missing addresses are `null` and each song counts
  the blocks it references.

## Layout

- `aykit/ay.py` – pointers, header, songs
- `ayinfo.py` – command line

## Running

    python3 ayinfo.py examples/agent_x.ay examples/compilation.ay
    python3 -m unittest discover -s tests
