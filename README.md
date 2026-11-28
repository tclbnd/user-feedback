# rasckit

Frame check for RemotelyAnywhere screen recordings (the `RASC` video codec
used for remote-support session captures). The rip tool stores the video
packets of one recording as a `.rasc` pack; `rascinfo.py` decodes every
packet the way the RASC codec does and prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `RASCPACK` |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet exactly as stored in the AVI |

A packet is either `EMPT` (nothing changed) or a series of chunks: a tag,
optionally preceded by `KBND` / `BNDL`, a `u32` little-endian size and the
body. `FINT`/`INIT` describe the screen (size, 8-, 16- or 32-bit pixels and
the palette), `KFRM` carries zlib-compressed key frame data, `DLTA` updates a
rectangle, `MOVE` copies, clears or moves rectangles, `MOUS` and `MPOS` give
the cursor image and position. The codec keeps two screen buffers; the
second one is the picture that is shown.

## Output

| field | meaning |
|---|---|
| `frames` | one object per packet: `state` (`empty` for an `EMPT` packet, `decoded`, or `error` when the codec rejects the packet); decoded packets also give `width`, `height` and `crc32` (lower-case hex) of the shown picture, cursor included, as RGB, three bytes per pixel, rows top to bottom, 5-bit components widened to 8 bits by repeating their top bits (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `rasckit/rasc.py` – pack reader, chunk decoders and cursor drawing
- `rascinfo.py` – command line

## Running

    python3 rascinfo.py examples/session.rasc
    python3 -m unittest discover -s tests
