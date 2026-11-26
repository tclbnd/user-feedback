# xcfkit

Check for the GIMP project files (`.xcf`) the art team keeps for UI mock-ups.
`xcfinfo.py` reads each file and prints one JSON summary with the decoded
pixels of every layer. Files from the XCF versions `file` and `v001`–`v003`
occur here.

## Output

| field | meaning |
|---|---|
| `width`, `height` | canvas size |
| `base_type` | `"RGB"`, `"GRAY"` or `"INDEXED"` |
| `compression` | `"none"`, `"rle"` or `"zlib"` |
| `colormap_colors` | colours in the image colormap, 0 without one |
| `layers` | list in file order (topmost first) |

Each layer:

| field | meaning |
|---|---|
| `name` | layer name |
| `type` | `"RGB"`, `"RGBA"`, `"GRAY"`, `"GRAYA"`, `"INDEXED"` or `"INDEXEDA"` |
| `width`, `height` | layer size |
| `x`, `y` | layer offsets on the canvas |
| `opacity` | 0–255 |
| `visible` | boolean |
| `first_pixel`, `last_pixel` | the top-left and bottom-right pixel's stored bytes as lower-case hex |
| `clear_pixels` | pixels with alpha 0 (layer types with alpha), else `null` |
| `crc32` | CRC-32 of the layer's pixel bytes, rows top to bottom, pixels interleaved, 8 lower-case hex digits |

## Layout

- `xcfkit/xcf.py` – header, properties, layers, tiles
- `xcfinfo.py` – command line

## Running

    python3 xcfinfo.py examples/menu.xcf examples/hud.xcf
    python3 -m unittest discover -s tests
