# xwdkit

Check for the X Window screen dumps (`.xwd`) collected from the test
workstations. `xwdinfo.py` prints one JSON summary per dump, used to verify
the screenshots before they are attached to test tickets.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `depth`, `bits_per_pixel` | from the header (integers) |
| `byte_order` | `msb` or `lsb` |
| `visual` | `static_gray`, `gray_scale`, `static_color`, `pseudo_color`, `true_color` or `direct_color` |
| `window_name` | the dumped window's name, `null` without one |
| `colors` | the number of colormap entries stored in the file (an integer) |
| `image_bytes` | bytes of pixel data the header implies (an integer) |
| `complete` | `true` when the file holds all of the pixel data |
| `points` | the colour (`#rrggbb`) at `top_left`, `top_right`, `center` (width / 2, height / 2) and `bottom_right`; `null` when the pixel data is incomplete |

Colours of colormapped visuals come from the colormap entry for the pixel
value (16-bit components, the high byte kept; `#000000` when the colormap
has no entry for the value). True colour pixels are split with the red,
green and blue masks and each component is widened to 8 bits as
`round(v × 255 / max)`, where `max` is the largest value the mask allows.
Only ZPixmap images are supported. The header, the window name, the
colormap and the pixel layout follow the XWD file format version 7.

## Layout

- `xwdkit/xwd.py` – header, colormap, pixels
- `xwdinfo.py` – command line

## Running

    python3 xwdinfo.py examples/xterm.xwd examples/desktop.xwd
    python3 -m unittest discover -s tests
