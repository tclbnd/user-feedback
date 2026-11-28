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
(skip, literal pixels or a copy from the player's 32 KB history of drawn
pixels, fill, long skip) and whose low six bits give a count. The picture,
the history and the palette are kept from packet to packet.

| top two bits | operation |
|---|---|
| `00` | short skip of 0–63 pixels; nothing is drawn or recorded |
| `01` | literal run: count 1–63 pixels follow; count 0 marks a history copy — a `u16` little-endian absolute history offset and then one count byte, each pixel read from the history |
| `10` | fill of 1–63 pixels with the following byte |
| `11` | long skip: the low six bits form the high byte and the next byte the low byte of a 14-bit skip count |

Every pixel a packet draws — literal, fill or history copy — is appended to
the 32 KB history ring at its running cursor, wrapping modulo 32768; skips
move only the draw cursor and leave the history alone. Each packet continues
the running picture, history cursor and palette (a packet without the
palette flag keeps the previous palette).

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
