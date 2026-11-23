# dpxkit

Intake check for the film scan frames (`.dpx`) that arrive from the scanning
facilities. `dpxinfo.py` reads each frame's header and prints one JSON summary,
used to confirm frame size, pixel format, frame rate and timecode before the
grade.

## Output

| field | meaning |
|---|---|
| `endian` | `big` or `little`, as the file declares |
| `version`, `creator`, `created` | header text; `created` as `YYYY-MM-DDTHH:MM:SS` |
| `image_offset`, `file_size` | from the file header |
| `encrypted` | the encryption key is set |
| `orientation`, `width`, `height` | from the image header (`width` is pixels per line, `height` lines per element) |
| `display_width`, `display_height` | size of the picture as displayed (orientations 4–7 store it transposed) |
| `elements` | per image element: `descriptor` (`RGB`, `RGBA`, `ABGR`, `Y`, `CbYCrY`, `CbYACrYA`, `CbYCr`, `CbYCrA`, `R`, `G`, `B`, `A`), `bit_depth`, `packing` (`packed`, `filled_a`, `filled_b`), `rle`, `offset`, `data_bytes` (size of the element's image data with line padding, `null` for RLE), `signed` (the element's data sign field is 1), `transfer` (`user_defined`, `printing_density`, `linear`, `logarithmic`, `unspecified_video`, `smpte_274m`, `itu_r_709`, `itu_r_601_625`, `itu_r_601_525`, `ntsc_composite`, `pal_composite`, `z_linear`, `z_homogeneous` for codes 0–12, else `type_N`), `colorimetric` (the element's colorimetric specification by the same names, for the codes SMPTE ST 268 defines for it, else `type_N`) |
| `pixel_aspect` | horizontal : vertical pixel aspect ratio from the image source header, as a reduced fraction string |
| `frame_position`, `sequence_length` | frame number in the sequence and frames in the sequence, from the film header |
| `frame_rate` | frames per second from the film header, or from the television header when the film header leaves it undefined |
| `timecode` | SMPTE timecode from the television header, `HH:MM:SS:FF` |
| `interlaced` | from the television header |
| `truncated` | the file ends before the end of an element's image data |

Header fields that the file leaves undefined are reported as `null`, and so are the film and television fields of a file whose header gives no room for them.

## Layout

- `dpxkit/dpx.py` – headers and element sizes
- `dpxinfo.py` – command line

## Running

    python3 dpxinfo.py examples/reel2_086400.dpx examples/telecine_000120.dpx
    python3 -m unittest discover -s tests
