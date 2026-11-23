# mxfkit

Ingest report for the MXF files that arrive from cameras, edit suites and the
playout archive. `mxfinfo.py` walks each file's KLV structure and prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `run_in` | bytes before the header partition pack |
| `operational_pattern` | `OP1a` … `OP3c`, `OP-Atom`, or `unknown` |
| `partitions` | in file order: `kind` (`header`, `body`, `footer`), `closed`, `complete`, `body_sid`, `index_sid` |
| `rip` | the file ends with a Random Index Pack |
| `edit_rate`, `duration_edit_units`, `duration` | from the material package's picture track; when its duration is unknown, from the picture descriptor's container duration; `duration` in seconds as a fraction string; `null` when unknown |
| `start_timecode` | start timecode of the material package's timecode track, `HH:MM:SS:FF` (`;` before the frames for drop-frame timecode), else `null` |
| `video` | per picture descriptor: `width`, `height` (frame height), `interlaced`, `bit_depth`, `sample_rate` |
| `audio` | per sound descriptor: `sample_rate`, `channels`, `bits` |
| `essence` | essence element counts by item type: `picture`, `sound`, `data`, `compound`, `system` |
| `essence_bytes` | essence bytes by the same item types (the values of the essence elements, without their keys and lengths) |
| `fill_bytes` | bytes in KLV fill items (values only; the fill key's version byte varies between writers) |
| `index` | `segments` (distinct index table segments), `edit_units` they cover, `constant_bytes` (edit unit byte count for constant-size essence, else `null`) |

Header metadata is taken from the best copy in the file: a closed and complete
partition's copy is preferred over one in an open or incomplete partition.

## Layout

- `mxfkit/mxf.py` – KLV, partitions, metadata, index
- `mxfinfo.py` – command line

## Running

    python3 mxfinfo.py examples/news_1080i.mxf examples/promo_720p.mxf
    python3 -m unittest discover -s tests
