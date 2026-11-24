# cdtextkit

Check for the CD-Text read from the label's reference discs (the drive's
CD-Text pack dump, `.cdt`, with or without the 4-byte header the drive puts
in front). `cdtextinfo.py` prints one JSON summary per dump, used to compare
the disc's text with the release metadata.

## Output

| field | meaning |
|---|---|
| `packs` | the number of 18-byte packs |
| `crc_errors` | packs whose CRC does not match |
| `blocks` | one entry per language block present, in block order |

Each block has `block` (0–7), `charset` (`iso-8859-1`, `ascii`, `ms-jis`,
else `type_N`; `null` without size information), `language` (the language
code for this block from the size information, an integer, `null` without
it), `double_byte` (`true` for double-byte text), `album` (the disc's own
strings by field: `title`, `performer`, `songwriter`, `composer`,
`arranger`, `message`, `disc_id`, `upc`, plus `genre` and `genre_text`
from a genre pack) and `tracks` (per track with any text, sorted by
`number`: the same text fields and `isrc`, empty strings as `null`).

Packs, CRCs, the way strings run across packs and repeat, track numbering,
language blocks, character sets and the size information follow the CD-Text
specification (Red Book / MMC).

## Layout

- `cdtextkit/cdtext.py` – packs, strings, blocks
- `cdtextinfo.py` – command line

## Running

    python3 cdtextinfo.py examples/harbour.cdt examples/anthology.cdt
    python3 -m unittest discover -s tests
