# mszhkit

Frame check for AVI clips coded with the LCL "Avimszh" codec (FourCC `MSZH`),
which a number of late-90s Windows titles used for intros and cut-scenes. The
rip tool stores the codec header and the frames of one clip as an `.mszh`
pack; `mszhinfo.py` decodes every frame the way the Avimszh codec does and
prints one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `MSZHPACK` |
| 2 + 2 | width, height (`u16` little-endian) |
| 8 | codec header exactly as stored after the AVI's bitmap header: bytes 0–3 unused, byte 4 image type, byte 5 compression, byte 6 flags, byte 7 codec (1 for MSZH) |
| 2 | frame count (`u16` little-endian) |
| | for each frame its size (`u32` little-endian) and the frame as stored in the AVI |

Image types: 0 YUV 1:1:1, 1 YUV 4:2:2, 2 RGB 24, 3 YUV 4:1:1, 4 YUV 2:1:1,
5 YUV 4:2:0. Compression: 0 MSZH, 1 none. Flag bit 0 marks the
multithreaded encoder, bit 1 null-frame insertion.

## Output

| field | meaning |
|---|---|
| `width`, `height` | header values |
| `type` | `yuv111`, `yuv422`, `rgb24`, `yuv411`, `yuv211` or `yuv420` |
| `frames` | one object per frame: `state` (`decoded`, `repeat` for a null frame that repeats the previous picture, `error` when the codec rejects the frame) and `crc32` (lower-case hex) of the picture, or `null` when there is none |

The picture is hashed as the codec outputs it, rows top to bottom: for the
YUV types the Y plane, then U, then V (chroma at the type's resolution: full
for 1:1:1, half width for 4:2:2 and 2:1:1, quarter width for 4:1:1, half
width and height for 4:2:0, samples 0–255 with 128 as neutral); for RGB 24
three bytes per pixel in R, G, B order.

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `mszhkit/mszh.py` – pack reader, MSZH decompressor and image layouts
- `mszhinfo.py` – command line

## Running

    python3 mszhinfo.py examples/intro.mszh examples/title.mszh
    python3 -m unittest discover -s tests
