# exrkit

Intake check for the OpenEXR plates and renders that come back from the VFX
vendors. `exrinfo.py` reads each file's header and prints one JSON summary,
used to check channels, windows, compression and layout before conform.

## Output

| field | meaning |
|---|---|
| `version` | the file format version (the low 8 bits of the version field, an integer) |
| `tiled`, `long_names`, `deep`, `multipart` | the version field's flags (`true`/`false`) |
| `parts` | one entry per header (a single-part file has one) |

Each part has:

| field | meaning |
|---|---|
| `name`, `type` | the `name` and `type` attributes (`null` name without one; a single-part file without `type` is `scanlineimage`, or `tiledimage` when the tiled flag is set) |
| `channels` | per channel in file order: `name`, `type` (`uint`, `half`, `float`, else `type_N`) and `sampling` (`[x, y]`) |
| `compression` | `none`, `rle`, `zips`, `zip`, `piz`, `pxr24`, `b44`, `b44a`, `dwaa`, `dwab` |
| `data_window`, `display_window` | `{"origin": [x, y], "size": [width, height]}` from the box2i attributes |
| `line_order` | `increasing_y`, `decreasing_y` or `random_y` |
| `pixel_aspect` | rounded to 6 decimals |
| `tiles` | for a tile description: `{"size": [x, y], "level_mode", "rounding"}` with `one_level` / `mipmap` / `ripmap` and `down` / `up`; else `null` |
| `chunks` | for a `scanlineimage` part: the number of scan-line blocks the data window needs at its compression; else `null` |
| `frame_rate` | `framesPerSecond` as a fraction string, `null` without one |
| `custom` | names of the attributes this list does not describe, sorted |

The header, attribute encoding, version flags and the scan lines per block
of each compression follow the OpenEXR file layout. Attributes of any type
are stored as name, type name, size and value.

## Layout

- `exrkit/exr.py` – version field, attributes, parts
- `exrinfo.py` – command line

## Running

    python3 exrinfo.py examples/plate.exr examples/render.exr
    python3 -m unittest discover -s tests
