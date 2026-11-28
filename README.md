# mvckit

Frame check for clips coded with the Silicon Graphics Motion Video
Compressor (`MVC1` and `MVC2`), as written by SGI capture and editing tools.
The rip tool stores the codec header and frames of one clip as a pack;
`mvcinfo.py` decodes every frame the way the MVC decoders do and prints one
JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `MVCPACK1` |
| 4 | codec FourCC, `MVC1` or `MVC2` |
| 2 + 2 | width, height as given in the stream header (`u16` little-endian) |
| 2 + n | length (`u16` little-endian) and the codec's extra data as stored in the file |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame exactly as stored |

Both codecs work on 4 × 4 blocks. MVC1 frames are RGB555 blocks with a
16-bit mask and two or eight colours; MVC2 frames start with their own size,
a flags byte and a palette, followed by grey, direct-colour, two-colour and
eight-colour blocks. Clips whose extra data ends with `BottomUp` are stored
upside down.

## Output

| field | meaning |
|---|---|
| `codec`, `width`, `height` | codec and the picture size the decoder uses |
| `frames` | one object per frame: `state` (`decoded`, or `error` when the decoder rejects the frame) and `crc32` (lower-case hex) of the picture as RGB, three bytes per pixel, rows top to bottom, 5-bit components widened to 8 bits by repeating their top bits (`null` for an error) |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `mvckit/mvc.py` – pack reader, MVC1 and MVC2 decoders
- `mvcinfo.py` – command line

## Running

    python3 mvcinfo.py examples/studio.mvc examples/logo.mvc
    python3 -m unittest discover -s tests
