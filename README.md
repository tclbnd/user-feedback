# webpkit

Check for the animated WebP stickers and banners delivered for the studio's
web store. `webpinfo.py` reads each file and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `canvas` | `{"width", "height"}` of the extended-format canvas, `null` for a simple file |
| `animated`, `alpha`, `icc`, `exif`, `xmp` | the extended-format feature flags (`true`/`false`) |
| `background` | the animation background colour as `#rrggbb`, `null` without an animation chunk |
| `background_alpha` | its alpha value (an integer 0–255), `null` without one |
| `loop_count` | the animation loop count (an integer, 0 meaning forever), `null` without one |
| `frames` | one entry per animation frame in file order |
| `duration` | sum of the frame durations in milliseconds (an integer) |
| `image` | `{"codec", "width", "height", "alpha"}` of a still image's bitstream, `null` for animations |

Each frame has `x`, `y`, `width`, `height` (pixels on the canvas), `duration`
(milliseconds), `blend` (`true` when the frame is alpha-blended onto the
canvas), `dispose` (`true` when the frame area is cleared to the background
afterwards), `codec` (`lossy` or `lossless`, `null` without image data) and
`alpha` (`true` when the frame carries transparency, from an alpha chunk or the
lossless bitstream). Codec dimensions come from the VP8 or VP8L bitstream
header.

Chunks and fields follow the WebP container specification.

## Layout

- `webpkit/webp.py` – RIFF chunks, extended format, animation, bitstream headers
- `webpinfo.py` – command line

## Running

    python3 webpinfo.py examples/sticker.webp examples/banner.webp
    python3 -m unittest discover -s tests
