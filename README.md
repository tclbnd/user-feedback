# apekit

Catalogue checker for the Monkey's Audio (`.ape`) rips in the music archive,
which span every encoder version since the late 1990s. `apeinfo.py` reads the
file header and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `id3v2_bytes` | bytes of ID3v2 tags in front of `MAC ` (0 if none) |
| `version` | encoder version as `major.minor` (`3990` → `3.99`) |
| `layout` | `descriptor` for files with the APE descriptor, `old` for files with the older single header |
| `compression` | `fast`, `normal`, `high`, `extra high` or `insane` |
| `channels`, `bits`, `sample_rate` | audio format |
| `blocks_per_frame`, `total_frames`, `final_frame_blocks` | frame layout (for old files, blocks per frame as that encoder version used it) |
| `total_blocks` | audio blocks (samples per channel) in the file |
| `duration` | `total_blocks` ÷ `sample_rate`, as a fraction string |
| `seek_entries` | seek table entries |
| `wav_header_bytes` | bytes of the original WAV header stored in the file (0 when none is stored) |
| `frame_data_bytes`, `md5` | from the descriptor (`null` for old files) |
| `peak_level` | from old headers that carry one, else `null` |

## Layout

- `apekit/mac.py` – file header
- `apeinfo.py` – command line

## Running

    python3 apeinfo.py examples/remaster.ape examples/1999_rip.ape
    python3 -m unittest discover -s tests
