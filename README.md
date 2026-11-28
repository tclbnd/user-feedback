# imxkit

Frame check for the IMX animations of Simbiosis Interactive's mid-90s games
(320 × 160, 256 colours). The rip tool stores the video packets of one movie
as an `.imx` pack; `imxinfo.py` decodes every packet the way the game's
player does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `IMXPACK1` |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian), then a flag byte (bit 0: a palette of 256 RGB triples, 8 bits per component, follows) and the video data exactly as stored in the movie |

The video data is a series of codes whose top two bits select the operation
(skip, literal pixels or a copy from the player's 32 KB history of literal
pixels, fill, long skip) and whose low six bits give a count. The picture,
the history and the palette are kept from packet to packet.

## Output

| field | meaning |
|---|---|
| `width`, `height` | 320, 160 |
| `frames` | one object per packet: `state` (`decoded`, or `error` when the player rejects the packet), `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom (`null` for an error), and `history`, the number of bytes held in the history after the packet |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `imxkit/imx.py` – pack reader and packet decoder
- `imxinfo.py` – command line

## Running

    python3 imxinfo.py examples/film.imx
    python3 -m unittest discover -s tests
