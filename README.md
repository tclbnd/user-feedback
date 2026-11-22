# psfkit

Catalogue checker for the console music rips in the archive that use the PSF
family of formats (`.psf`, `.minipsf`, `.psf2`, `.ssf`, `.dsf`, `.usf`,
`.gsf`, `.snsf`, `.qsf` and their libraries). `psfinfo.py` reads each file and
prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `system` | PlayStation, PlayStation 2, Saturn, Dreamcast, Mega Drive, Nintendo 64, Game Boy Advance, Super NES or Capcom QSound; else the version byte as `0x..` |
| `reserved_bytes`, `compressed_bytes` | sizes of the reserved area and of the compressed program |
| `crc_ok` | the CRC-32 of the compressed program matches the header (`null` without a program) |
| `program_bytes` | size of the program after decompression (0 without a program) |
| `title`, `artist`, `game`, `year` | from the tag, `null` when absent |
| `length`, `fade` | in seconds as fraction strings, from tag values written as `[[h:]m:]s[.d]` (`,` also works as the decimal mark); `null` when absent or unreadable |
| `libs` | library files the rip depends on, in loading order (`_lib`, `_lib2`, `_lib3`, … up to the first missing number) |
| `utf8` | the tag says its text is UTF-8 (otherwise the text is Windows-1252) |
| `tags` | every other tag by lower-case name |

Tag names are not case sensitive, spaces and control characters around names
and values are ignored, and a name repeated on several lines gives a value with
one line per occurrence.

## Layout

- `psfkit/psf.py` – header, program, tag
- `psfinfo.py` – command line

## Running

    python3 psfinfo.py examples/opening.psf examples/stage2.minigsf
    python3 -m unittest discover -s tests
