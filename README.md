# pkmtex

Check for the PKM textures (`.pkm`) the art team exports for the mobile build.
`pkminfo.py` prints one JSON summary per texture with the decoded colours as
an OpenGL ES 3.0 GPU samples them.

## File layout

`PKM `, the version `10` or `20`, then five big-endian 16-bit fields: data
type, padded width, padded height, width, height. The padded size is the
image size rounded up to whole 4 × 4 blocks. Blocks follow in rows, 8 bytes
each. Version `10` files always hold ETC1 RGB; in version `20` files data type
0 is ETC1 RGB and 1 is ETC2 RGB. Other data types are not used and are an
error.

## Output

| field | meaning |
|---|---|
| `format` | `"ETC1"` or `"ETC2"` |
| `width`, `height` | the image size (not the padded size) |
| `blocks` | 4 × 4 blocks in the padded image |
| `complete` | `true` when every block is in the file |
| `modes` | object counting blocks per mode: `individual`, `differential`, `T`, `H`, `planar` |
| `colors_used` | distinct colours inside the image size |
| `first_row`, `last_row` | top and bottom image rows as runs `[["#rrggbb", count], ...]`, padding excluded |

`modes`, `colors_used` and the rows are `null` when the texture is incomplete
or has a zero width or height.

## Layout

- `pkmtex/etc.py` – header, block decoding
- `pkminfo.py` – command line

## Running

    python3 pkminfo.py examples/ui_atlas.pkm examples/sky.pkm
    python3 -m unittest discover -s tests
