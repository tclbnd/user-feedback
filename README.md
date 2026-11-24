# sgikit

Check for the SGI image files (`.rgb`, `.bw`, `.sgi`) in the render farm's
texture archive. `sgiinfo.py` prints one JSON summary per image, used to
check textures before they are converted for the asset pipeline.

## Output

| field | meaning |
|---|---|
| `storage` | `verbatim` or `rle` |
| `bytes_per_channel`, `dimension` | from the header (integers) |
| `width`, `height`, `channels` | the image size as the dimension defines it (integers) |
| `name` | the image name, `null` when empty |
| `pixel_range` | `[pixmin, pixmax]` from the header |
| `colormap` | `normal`, `dithered`, `screen`, `colormap`, else `type_N` |
| `shared_rows` | for RLE images: how many table entries point at a row already used by another entry (an integer); 0 for verbatim images |
| `complete` | `true` when every row of every channel decodes to the full width |
| `points` | the channel values (a list of integers, one per channel) at `top_left`, `top_right`, `bottom_left` and `center` (`width // 2`, `height // 2` from the top) of the picture as displayed; `null` when that row does not decode |
| `max_value` | the largest channel value in the decoded rows (an integer, `null` when nothing decodes) |

The header, the storage formats, the RLE offset and length tables, the RLE
packets, 1- and 2-byte channels and the row order follow the SGI image file
format (Paul Haeberli's specification, version 1.0).

## Layout

- `sgikit/sgi.py` – header, tables, rows
- `sgiinfo.py` – command line

## Running

    python3 sgiinfo.py examples/marble.rgb examples/height.bw
    python3 -m unittest discover -s tests
