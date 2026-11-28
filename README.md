# bmvkit

Frame check for the BMV cut-scenes of Discworld II (640 × 429, 256 colours).
The rip tool stores the packets of one movie as a `.bmv` pack; `bmvinfo.py`
decodes every packet the way the game's player does and prints one JSON
summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `BMVPACK1` |
| 2 + 2 | width, height (`u16` little-endian, always 640 and 429) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet exactly as stored in the movie |

A packet starts with a type byte: the low two bits give the frame kind
(3 is an intra frame), 0x04 a screen offset (`s16` little-endian), 0x08 a
palette of 256 RGB triples, 0x10 a command block (0x80: the short form used
for text), 0x20 audio blobs. The rest is the picture data: a stream of
nibble-coded runs that copy from an offset in the picture, take literal
bytes, or repeat the last pixel. The player keeps the picture from packet to
packet in a buffer with one spare row above the screen.

## Output

| field | meaning |
|---|---|
| `width`, `height` | 640, 429 |
| `frames` | one object per packet: `state` (`decoded`, or `error` when the player rejects the packet) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `bmvkit/bmv.py` – pack reader, packet header and run decoder
- `bmvinfo.py` – command line

## Running

    python3 bmvinfo.py examples/scene.bmv
    python3 -m unittest discover -s tests
