# cdgkit

Graphics check for the karaoke library's CD+G tracks (the `.cdg` subcode file
ripped next to each song). `cdginfo.py` replays the TV graphics instructions of
each file and prints one JSON summary, used to catch lyric screens that are
missing, mistimed or drawn in the wrong colours.

## Output

| field | meaning |
|---|---|
| `duration` | playing time in seconds, as a fraction string (one packet per 1/300 s) |
| `packets`, `graphics_packets` | 24-byte packets in the file, and those carrying TV graphics |
| `instructions` | graphics instruction counts by name: `memory_preset`, `border_preset`, `tile_block`, `tile_block_xor`, `scroll_preset`, `scroll_copy`, `define_transparent`, `load_colors_low`, `load_colors_high`, else `type_N` |
| `first_tile` | time of the first tile drawn on screen (seconds, fraction string), `null` without one |
| `clears` | times of the screen clears (one per memory preset group, not per repeat) |
| `colors` | the 16-entry colour table at the end, `#rrggbb` (4-bit components widened as `v * 17`) |
| `border`, `transparent` | border colour index and the colour index defined as transparent (`null` if never defined) |
| `pixels` | colour index counts over the whole 300 × 216 screen memory at the end, border area included |
| `visible_pixels` | the same counts over the visible area only (the 288 × 192 pixels inside the 6-pixel left and right and 12-pixel top and bottom border) |
| `tiles_drawn` | tile instructions (normal and XOR) that drew on the screen (a tile at row 18 or more or column 50 or more draws nothing) |
| `scroll_offset` | the fine offset of the display window after the last scroll instruction: `h` (0–5 pixels) and `v` (0–11 pixels); both 0 without a scroll |

The subcode bytes carry the graphics in their low six bits; the top two bits
belong to the P and Q channels.

## Layout

- `cdgkit/cdg.py` – packets, instructions, screen memory
- `cdginfo.py` – command line

## Running

    python3 cdginfo.py examples/ballad.cdg examples/duet.cdg
    python3 -m unittest discover -s tests
