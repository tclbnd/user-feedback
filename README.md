# tgakit

Intake check for the TGA frames and textures that come back from the paint,
matte and game-art vendors. `tgainfo.py` prints one JSON summary per image,
used to check type, orientation, pixel data and the TGA 2.0 extension area.

## Output

| field | meaning |
|---|---|
| `type` | `none`, `colormapped`, `truecolor`, `grayscale`, `rle_colormapped`, `rle_truecolor`, `rle_grayscale` |
| `width`, `height`, `depth` | from the header (integers) |
| `image_id` | the image ID field as text, `null` when empty |
| `colormap` | `{"first", "length", "entry_bits"}` for an image with a colour map, else `null` |
| `origin` | the first stored pixel's corner: `bottom_left`, `bottom_right`, `top_left` or `top_right` |
| `alpha_bits` | attribute bits per pixel from the descriptor (an integer) |
| `pixels`, `complete` | the number of pixels the image data yields (at most width × height), and whether that is all of them |
| `rle_packets`, `crossing_packets` | for RLE images: the number of packets, and of packets that run on into the next scan line; else `null` |
| `corner_pixel` | the colour shown at the picture's top-left corner as `#rrggbbaa` (colour-mapped pixels through the map, 15/16-bit pixels widened from 5 bits by repeating the top bits, alpha 255 where the image has none), `null` when the image data is incomplete |
| `version` | 2 when the file ends with the TGA 2.0 footer, else 1 |
| `extension` | for a TGA 2.0 extension area: `author`, `comments`, `timestamp` (`YYYY-MM-DDTHH:MM:SS`, `null` when not set), `job`, `software`, `pixel_aspect` and `gamma` (fraction strings, `null` when the denominator is 0) and `attribute_type` (`no_alpha`, `undefined_ignore`, `undefined_retain`, `alpha`, `premultiplied_alpha`); else `null` |

Header, image ID, colour map, pixel storage, RLE packets, the image
descriptor and the TGA 2.0 footer and extension area follow the Truevision
TGA file format specification 2.0.

## Layout

- `tgakit/tga.py` – header, colour map, pixels, footer
- `tgainfo.py` – command line

## Running

    python3 tgainfo.py examples/matte.tga examples/texture.tga
    python3 -m unittest discover -s tests
