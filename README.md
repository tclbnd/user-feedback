# mp4kit

Library checker for the audiobook and concert recordings kept as MP4 / M4A.
`mp4info.py` reads the movie metadata of each file and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `timescale`, `duration` | from the movie header; `duration` in seconds as a fraction string |
| `tags` | the file's iTunes metadata items (`moov/udta/meta/ilst`) by item name; see below |
| `chapters` | Nero chapter list (`chpl`): `start` in seconds as a fraction string, `title` |

Tag values:

- text items as strings, integer items as numbers;
- `trkn` and `disk` as `[number, total]`, `total` is `null` when not set;
- `gnre` is reported as `©gen` with the ID3v1 genre name;
- `covr` as a list with one `{"format": "jpeg" | "png", "bytes": N}` per picture;
- freeform `----` items under `mean:name`, for example `com.apple.iTunes:iTunNORM`;
- other binary values as hex.

Item names that start with the © sign keep it (`©nam`, `©ART`, ...).

## Layout

- `mp4kit/tags.py` – boxes, items, chapters
- `mp4info.py` – command line

## Running

    python3 mp4info.py examples/audiobook.m4b examples/concert.m4a
    python3 -m unittest discover -s tests
