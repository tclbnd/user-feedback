# sidkit

Catalogue check for the Commodore 64 music collection (`.sid` files, PSID and
RSID). `sidinfo.py` prints one JSON summary per tune, used to check memory
layout, song counts and playback settings before tunes go into the player.

## Output

| field | meaning |
|---|---|
| `format`, `version` | `PSID` or `RSID`, and the header version (an integer) |
| `name`, `author`, `released` | the header strings (Latin-1, up to the first NUL), `null` when empty |
| `load_address`, `end_address` | C64 addresses as `$XXXX` (upper-case hex): where the data loads and its last byte; `end_address` is `null` without data |
| `data_bytes` | the number of C64 data bytes (an integer) |
| `init_address`, `play_address` | `$XXXX`; `play_address` is `null` when the header gives 0 |
| `songs`, `start_song` | integers from the header |
| `speeds` | one entry per song: `vbi` or `cia` |
| `clock` | `unknown`, `PAL`, `NTSC` or `PAL and NTSC`; `null` for version 1 |
| `sid_model` | one entry per chip: `unknown`, `6581`, `8580` or `6581 and 8580`; `null` for version 1 |
| `chips` | the SID chip addresses as `$XXXX`, the standard `$D400` first |
| `player` | `compute_mus`, `playsid` (PSID) or `c64_basic` (RSID) from the flags, else `null` |
| `free_pages` | `[first, last]` addresses (`$XX00`, `$XXFF`) of the free memory range the header reserves for the player, `[]` when it says there is none, `null` when it does not say (or for version 1) |

Header layout, the load address, speed bits, flag bits and the second and
third SID addresses follow the PSID/RSID file format (versions 1–4).

## Layout

- `sidkit/sid.py` – header, addresses, flags
- `sidinfo.py` – command line

## Running

    python3 sidinfo.py examples/commando.sid examples/stereo_demo.sid
    python3 -m unittest discover -s tests
