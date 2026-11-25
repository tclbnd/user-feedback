# vtfkit

Check for the Valve texture files (`.vtf`) in the map-making asset store.
`vtfinfo.py` prints one JSON summary per texture, used before textures are
packed into a map so that a truncated or mis-sized texture is caught early.

## Output

| field | meaning |
|---|---|
| `version` | `"7.N"` |
| `width`, `height`, `depth` | size of the largest mipmap (integers; `depth` is 1 for flat textures) |
| `format` | high-resolution image format, lower case (`dxt1`, `bgr888`, `rgba16161616f`, …) |
| `mipmaps`, `frames`, `faces` | counts (integers); `faces` is how many faces each frame stores |
| `thumbnail` | `[width, height]` of the low-resolution thumbnail, `null` without one |
| `thumbnail_format` | its image format, `null` without one |
| `image_bytes` | total bytes of high-resolution image data (all mipmaps, frames, faces and slices) |
| `largest_mip_offset` | file offset of the largest mipmap of the first frame, first face, first slice |
| `complete` | `true` when the file holds all of the high-resolution image data |

The header of each version, the thumbnail, the resource dictionary, the
image formats and the order of the image data follow the VTF format as the
Source engine reads it.

## Layout

- `vtfkit/vtf.py` – header, sizes, data layout
- `vtfinfo.py` – command line

## Running

    python3 vtfinfo.py examples/crate.vtf examples/skybox.vtf
    python3 -m unittest discover -s tests
