# rl2kit

Frame check for the RL2 cut-scenes of a few early-90s PC adventure games.
The extraction tool stores one RL2 movie as a frame pack (`.rl2`);
`rl2info.py` decodes every frame, the way the games' own player does, and
prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `RL2PAK` and two NULs |
| 2 + 2 | width, height (`u16` little-endian) |
| 2 | video base: offset of the first pixel the frames code (`u16` little-endian) |
| 768 | palette, 256 entries of red, green, blue as stored in the movie header |
| 4 + n | size of the background frame (`u32` little-endian, 0 when the movie has none) and the background frame as stored |
| 2 | frame count (`u16` little-endian) |
| | then for each frame its size (`u32` little-endian) and the frame data as stored |

Frames and the background frame use the RL2 run-length coding.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `video_base` | header values |
| `background` | whether the movie has a background frame |
| `frames` | one object per frame: `crc32` of the whole picture as RGB bytes (three per pixel, rows top to bottom, palette colours as the player shows them on screen), and `first_row` and `last_row`, the CRC-32 of the first and of the last row alone; all lower-case hex |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `rl2kit/rl2.py` – frame decoder
- `rl2info.py` – command line

## Running

    python3 rl2info.py examples/scene.rl2 examples/title.rl2
    python3 -m unittest discover -s tests
