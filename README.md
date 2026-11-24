# hgrkit

Check for the Apple II hi-res title screens (`.hgr`, the 8 KiB hi-res page
saved from $2000) in the retro game archive. `hgrinfo.py` prints one JSON
summary per screen, used to verify screens before conversion for the web
gallery.

## Output

| field | meaning |
|---|---|
| `bytes` | file size (8192, or 8184 for the common short BSAVE) |
| `points` | the colour at `top_left` (0, 0), `x7` (7, 0), `row_1` (0, 1), `row_8` (0, 8), `row_64` (0, 64), `center` (140, 96) and `bottom_right` (279, 191) on the 280 × 192 grid |
| `lit_pixels` | the number of lit pixels |
| `colors` | lit pixels counted by colour name |
| `group1_bytes` | bytes with the colour group bit set |
| `hole_bytes` | non-zero bytes in the page that are not shown on any line (an integer) |

Colours: an unlit pixel is `black`; a lit pixel with a lit neighbour on the
same line is `white`; a lone lit pixel is `violet` (even column) or `green`
(odd column) when its byte's group bit is clear, and `blue` (even) or
`orange` (odd) when it is set.

The screen memory layout and the pixel bits of each byte follow the Apple II
hi-res graphics hardware.

## Layout

- `hgrkit/hgr.py` – line addressing, pixels, colours
- `hgrinfo.py` – command line

## Running

    python3 hgrinfo.py examples/title.hgr examples/map.hgr
    python3 -m unittest discover -s tests
