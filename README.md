# apekit

Tag reader for the lossless music library (Monkey's Audio, WavPack and MP3
files tagged with APE tags). `apeinfo.py` prints one JSON summary of the APE
tag of each file.

## Output

| field | meaning |
|---|---|
| `version` | `1.000` (APEv1) or `2.000` (APEv2) |
| `has_header` | whether the tag also has a header in front of the items |
| `read_only` | tag-level read-only flag |
| `id3v1` | whether an ID3v1 tag follows the APE tag |
| `lyrics3_bytes` | bytes of a Lyrics3 v2.00 block between the APE tag and ID3v1 (0 if none) |
| `tag_bytes` | bytes of the whole APE tag (header, items and footer) |
| `audio_bytes` | bytes in front of the APE tag |
| `invalid_keys` | items skipped because their key is not a valid APE item key |
| `items` | in file order: `key`, `type` (`text`, `binary`, `locator`, `reserved`), `read_only`, and for text items `values` (a list), for cover art `filename` and `bytes` (image data), for other items `bytes` |

## Layout

- `apekit/ape.py` – footer, header, items
- `apeinfo.py` – command line

## Running

    python3 apeinfo.py examples/album_track.ape examples/podcast.mp3
    python3 -m unittest discover -s tests
