# objkit

Check for the CPS-1 object table snapshots (`.cpo`) the team saves from the
arcade emulator while re-drawing sprite scenes of Capcom CPS-1 games.
`objinfo.py` renders the sprite (OBJ) layer as the board would show it from
that table and prints one JSON summary per snapshot. Scroll layers and the
star field are not part of the snapshot.

## File layout

`CPS1OBJ` and a NUL, the 2 KB object RAM exactly as the 68000 sees it
(256 entries of four 16-bit words: X, Y, tile code, attributes), then the
sprite graphics ROM as whole 16 × 16 tiles of 128 bytes in the board's own
ROM layout, tile 0 first. Tile codes past the end of the ROM draw nothing.

## Picture

The visible area of the 512 × 262 frame is X 64–447 and Y 16–239, a picture
of 384 × 224 pixels. A pixel holds `palette * 16 + pen` (palette 0–31 from
the attributes), or 65535 where no sprite pixel shows.

## Output

| field | meaning |
|---|---|
| `entries` | entries before the end of the list |
| `end_marker` | index of the entry that ends the list, or `null` |
| `sprites_drawn` | entries with at least one pixel in the picture |
| `tiles_drawn` | 16 × 16 tiles with at least one pixel in the picture |
| `pixels`, `colours_used` | sprite pixels in the picture and distinct values among them |
| `bbox` | `[x0, y0, x1, y1]` of sprite pixels in picture coordinates, or `null` |
| `picture_crc32` | CRC-32 of the picture as unsigned 16-bit little-endian values, row by row, lower-case hex |

## Layout

- `objkit/cps1obj.py` – OBJ table walker and renderer
- `objinfo.py` – command line

## Running

    python3 objinfo.py examples/stage.cpo examples/boss.cpo
    python3 -m unittest discover -s tests
