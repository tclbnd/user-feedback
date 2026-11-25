# hdrkit

Check for the Radiance HDR pictures (`.hdr`) in the lighting team's
environment map library. `hdrinfo.py` prints one JSON summary per picture,
used to catch damaged or mis-exposed light probes before rendering.

## Output

| field | meaning |
|---|---|
| `format` | the FORMAT line (`32-bit_rle_rgbe` or `32-bit_rle_xyze`), `null` without one |
| `width`, `height` | size of the picture as displayed (integers) |
| `orientation` | the resolution string as written in the file |
| `exposure` | the combined EXPOSURE of the header (`%.4g`; `1` without one) |
| `scanlines` | how many scanlines use each encoding: `rle` (run-length per component), `old_rle` (repeat-pixel codes) or `flat` |
| `complete` | `true` when every scanline could be read |
| `points` | `top_left`, `top_right`, `bottom_left`, `bottom_right` and `center` (width / 2, height / 2) of the displayed picture, each `[r, g, b]` as `%.4f` strings, `null` for pixels not read |

Pixel values are the RGBE (or XYZE) mantissas converted as Radiance does,
`(m + 0.5) × 2^(e − 136)` (0 when `e` is 0), then divided by the exposure so
that they are the original radiances. The header, the resolution string and
its orientation, and the scanline encodings follow the Radiance picture
format.

## Layout

- `hdrkit/hdr.py` – header, scanlines, pixels
- `hdrinfo.py` – command line

## Running

    python3 hdrinfo.py examples/studio.hdr examples/sky.hdr
    python3 -m unittest discover -s tests
