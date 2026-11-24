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
| `songs`, `first_song` | the number of songs (the header stores the zero-based index of the last song, so one is added), and the first song's index as stored |
| `song_list` | per song: `name` (`null` when the song has no name), `channels` (the AY channel mapping of `a`, `b`, `c` and `noise`), `length_seconds` and `fade_seconds` (fraction strings at 50 interrupt frames per second; `length_seconds` is `null` when the stored length is 0, meaning the song length is unknown), `hi_reg`, `lo_reg` (initial register pair, integers; `null` without a register record), `stack`, `init`, `interrupt` (big-endian player points as `$XXXX`, `null` without player points; `init` is the address the player actually calls first), `blocks` (memory blocks loaded) and `block_bytes` (bytes those blocks actually load) |

Pointers are relative signed big-endian 16-bit offsets measured from the
pointer field itself (0 means none). The header, the null-terminated song list
(four bytes per entry: name pointer, song data pointer), the 14-byte song
records, the register and player point records and the memory block table all
follow that encoding. Every multi-byte value is big-endian.

A memory block declares a Z80 load address and a length; the bytes actually
loaded are capped by both the space left in the 64K Z80 address space
(`0x10000 - address`) and the bytes remaining in the file from the block's
data pointer. Blocks without readable file data are skipped. Several songs may
reference the same code block: each reference is counted independently. Songs
without a name, player points or register record still report the rest of
their data.

## Layout

- `aykit/ay.py` – pointers, header, songs, player points, memory blocks
- `ayinfo.py` – command line

## Running

    python3 ayinfo.py examples/agent_x.ay examples/compilation.ay
    python3 -m unittest discover -s tests
