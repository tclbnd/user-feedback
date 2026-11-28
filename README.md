# jvkit

Frame check for the JV cut-scenes of the Bitmap Brothers' mid-90s games. The
rip tool stores the packets of one movie as a `.jv` pack; `jvinfo.py` decodes
the video the way the game's player does and prints one JSON summary per
file.

## File layout

| bytes | content |
|---|---|
| 8 | `JVMOVIE` and a NUL |
| 2 + 2 | width, height (`u16` little-endian, multiples of 8) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet as stored in the movie |

A packet holds the video size (`u32` little-endian) and a video type byte,
the video data, and optionally a 256-colour palette of 6-bit red, green, blue
components. Frames are coded as 8 × 8 blocks split down to 2 × 2 with a
bit-packed quadtree. All frames are drawn onto one picture that starts as all
zero bytes; the palette starts black. A packet with a video size of 0 only
changes the palette and shows no frame.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per frame shown: `ok`, and `crc32` of the picture as RGB bytes (three per pixel, rows top to bottom, palette colours as the player shows them), lower-case hex; a damaged packet gives `ok` false and `crc32` null |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `jvkit/jv.py` – packet and block decoder
- `jvinfo.py` – command line

## Running

    python3 jvinfo.py examples/intro.jv examples/race.jv
    python3 -m unittest discover -s tests
