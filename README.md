# zmbvkit

Frame check for game footage recorded with DOSBox, which writes its video in
the Zip Motion Blocks Video (ZMBV) codec. The capture tool stores the video
chunks of one AVI stream in a frame pack (`.zmbv`); `zmbvinfo.py` decodes
every frame in order, the way DOSBox's own decoder does, and prints one JSON
summary per file.

## File layout

`ZMBVPAK` and a NUL, the picture width and height (`u16` little-endian
each), the frame count (`u16` little-endian), then for each frame its length
(`u32` little-endian) and the video chunk exactly as stored in the AVI.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `bits` | bits per pixel of the stream (8, 16 or 32) |
| `frames` | one object per frame, in order: `key` (true for a key frame) and `crc32`, the CRC-32 (lower-case hex) of the whole picture after that frame, rows top to bottom |

For 8-bit streams the picture is hashed as RGB bytes looked up in the current
palette (three bytes per pixel); for 16 and 32-bit streams the pixels are
hashed exactly as stored (2 or 4 bytes per pixel, little-endian).

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `zmbvkit/zmbv.py` – frame decoder
- `zmbvinfo.py` – command line

## Running

    python3 zmbvinfo.py examples/game.zmbv examples/desktop.zmbv
    python3 -m unittest discover -s tests
