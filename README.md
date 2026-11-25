# artkit

Check for the Build engine tile archives (`TILES000.ART` …) of the level
design team's mod. `artinfo.py` prints one JSON summary per archive, used to
verify tile sizes, animation settings and transparency before a release.

## Output

| field | meaning |
|---|---|
| `first_tile`, `last_tile` | tile numbers the archive covers (from its header) |
| `tiles` | how many tile slots the archive holds |
| `nonempty` | how many of them have pixels |
| `pixel_bytes` | total bytes of pixel data the sizes imply |
| `complete` | `true` when the file holds all of the pixel data |
| `tile_list` | one object per non-empty tile, in order |

Each entry of `tile_list` has:

| field | meaning |
|---|---|
| `tile` | the tile number (as the game refers to it) |
| `width`, `height` | tile size in pixels |
| `frames`, `animation`, `speed` | animation settings from the tile's picanm word; `animation` is `none`, `oscillate`, `forward` or `backward` |
| `offset` | `[x, y]` drawing offset from the picanm word (signed) |
| `transparent` | how many pixels use the transparent palette index 255 (`null` when the pixels are cut off) |
| `top_right`, `bottom_left` | palette index of the tile's top-right and bottom-left pixel (`null` when cut off) |

The header, the picanm bit fields and the pixel layout follow the Build
engine's ART format.

## Layout

- `artkit/art.py` – header, picanm, pixels
- `artinfo.py` – command line

## Running

    python3 artinfo.py examples/tiles000.art examples/tiles001.art
    python3 -m unittest discover -s tests
