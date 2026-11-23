# spckit

Catalogue checker for the Super Nintendo music rips in the chiptune archive
(`.spc`). `spcinfo.py` reads each file's header and tags and prints one JSON
summary.

## Output

| field | meaning |
|---|---|
| `has_id666` | the header says the file carries an ID666 tag; without it every tag field is `null` |
| `pc` | initial program counter, 4 hex digits |
| `id666_format` | `text` or `binary` (the two ID666 layouts), `null` without a tag |
| `title`, `game`, `artist`, `dumper`, `comments` | tag text, `null` when empty |
| `date` | date the file was dumped, `YYYY-MM-DD`, `null` when not set |
| `play_seconds`, `fade_ms` | play time before the fade and fade length from the ID666 tag |
| `emulator` | emulator used for the dump: `ZSNES`, `Snes9x` or `null` |
| `ost_title`, `ost_track` | soundtrack title and track (e.g. `7a`) from the extended tag |
| `length_seconds` | full play length in seconds as a fraction string (intro + loop × loop count + end from the extended tag when it has them, else `play_seconds`) |
| `xid6` | the file has an extended ID666 (xid6) tag |
| `ost_disc`, `publisher`, `copyright_year` | soundtrack disc, publisher and copyright year from the extended tag, else `null` |
| `muted_voices` | voices (0–7) the extended tag mutes, from its muted-voices bit field (bit 0 is voice 0), else `[]` |
| `amplification` | mixing level from the extended tag as a fraction string (0x10000 is 1), else `null` |

Values in the extended tag override the same fields of the ID666 tag (song name, game name, artist, dumper, date, comments, emulator, fade).

## Layout

- `spckit/spc.py` – header, ID666, extended ID666
- `spcinfo.py` – command line

## Running

    python3 spcinfo.py examples/overworld.spc examples/boss.spc
    python3 -m unittest discover -s tests
