# hnmkit

Check for the title and menu stills the team pulls out of Cryo Interactive
HNM4 movies (Dune, Lost Eden, Atlantis). The extraction tool keeps the
palette, intra-frame and sound chunks of one movie in order (`.hnc`);
`hnminfo.py` decodes every intra frame through the palette in force and
prints one JSON summary per file.

## File layout

`HNM4SEQ` and a NUL, the movie's HNM version byte (0x48 for HNM4, 0x4A for
HNM4A), a pad byte, width and height (`u16` little-endian each; the height is
even), the number of chunks (`u16` little-endian), then for each chunk its
length (`u32` little-endian) and the chunk exactly as stored in the movie,
starting with its own 4-byte size and 2-byte identifier (`PL`, `IZ`, `SD`).

The picture buffer and palette start as index 0 / black. Chunks shorter than
8 bytes, intra chunks shorter than 12 bytes and any other identifiers are
counted in `skipped`.

## Output

| field | meaning |
|---|---|
| `version`, `width`, `height` | from the file header |
| `palettes` | palette chunks applied |
| `frames` | one object per intra frame: `crc32` of the displayed picture as RGB24 (rows top to bottom, lower-case hex) and `colours` (distinct palette indices in it) |
| `skipped` | chunks not used |

## Layout

- `hnmkit/hnm4.py` – palette and intra-frame decoder
- `hnminfo.py` – command line

## Running

    python3 hnminfo.py examples/title.hnc examples/menu.hnc
    python3 -m unittest discover -s tests
