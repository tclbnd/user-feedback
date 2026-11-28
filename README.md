# vbkit

Frame check for the VB cut-scenes of Beam Software's mid-90s games. The rip
tool stores the packets of one movie as a `.vb` pack; `vbinfo.py` decodes
every frame the way the game's player does and prints one JSON summary per
file.

## File layout

| bytes | content |
|---|---|
| 8 | `VBMOVIE` and a NUL |
| 2 + 2 | width, height (`u16` little-endian, multiples of 4) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet as stored in the movie |

A packet starts with a `u16` flag word: bit 0 a global motion vector follows
(two signed `u16`), bit 3 a video chunk follows (`u32` size counting itself,
then the block data), bit 4 a palette chunk follows (`u32` size, first colour
number, colour count byte, then 8-bit red, green, blue triples). The picture
is coded as 4 × 4 blocks, left to right and top to bottom, with one type byte
for every four blocks; blocks can refer to the previous frame. Both pictures
and the palette start as all zero.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per packet: `ok`, and `crc32` of the frame as RGB bytes (three per pixel, rows top to bottom), lower-case hex; a packet the player rejects gives `ok` false and `crc32` null |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `vbkit/vb.py` – packet and block decoder
- `vbinfo.py` – command line

## Running

    python3 vbinfo.py examples/intro.vb examples/beach.vb
    python3 -m unittest discover -s tests
