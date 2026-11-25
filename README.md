# patchkit

Check for the picture lumps (sprites, wall patches, menu graphics) of the
mapping team's Doom-engine resource files. `patchinfo.py` prints one JSON
summary per extracted lump, used to catch broken or misaligned graphics.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size (integers) |
| `offset` | `[left, top]` drawing offset from the header (signed) |
| `posts` | number of posts (vertical runs of pixels) over all columns |
| `opaque_pixels` | number of picture pixels covered by posts |
| `longest_post` | length of the longest post |
| `shared_columns` | how many columns reuse the data of another column |
| `complete` | `true` when every column's posts could be read |
| `points` | palette index at `top_left`, `center` (width / 2, height / 2), `bottom_right` and `bottom_left`; `null` for transparent pixels |

The header, the column offsets and the post layout follow the Doom picture
format, including the tall-picture convention used by current source ports.

## Layout

- `patchkit/patch.py` – header, columns, posts
- `patchinfo.py` – command line

## Running

    python3 patchinfo.py examples/troo.lmp examples/wall.lmp
    python3 -m unittest discover -s tests
