# mmvkit

Frame check for the MM videos of American Laser Games' PC ports (the `.mm`
files of their light-gun titles). The rip tool stores the video packets of
one movie as an `.mm` pack; `mmvinfo.py` decodes every packet the way the
game's player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `MMPACK01` |
| 2 + 2 | width, height (`u16` little-endian, both even) |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet exactly as stored in the movie |

Each packet starts with a 6-byte preamble whose first word (`u16`
little-endian) is the packet type: `0x31` palette, `0x08` intra, `0x05`
inter, and the half-resolution variants `0x0C`/`0x0D` (half width) and
`0x0E`/`0x0F` (half width and height). Intra frames are run-length coded,
inter frames update pixels selected by bit masks. The picture is kept from
packet to packet.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `frames` | one object per packet: `state` (`palette` for a palette packet, `decoded` for a picture, `error` when the player rejects the packet) and, for pictures, `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `mmvkit/mmv.py` – pack reader, palette, intra and inter decoders
- `mmvinfo.py` – command line

## Running

    python3 mmvinfo.py examples/scene.mm
    python3 -m unittest discover -s tests
