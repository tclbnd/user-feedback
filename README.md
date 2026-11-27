# wsakit

Frame check for the Westwood WSA animations used in the menus and briefings of
Westwood's mid-90s strategy games. `wsainfo.py` decodes every frame of a
`.wsa` file the way the game's own player does and prints one JSON summary per
file.

## File layout

| bytes | content |
|---|---|
| 2 | frame count (`u16` little-endian, as are all the header words) |
| 2 + 2 | x, y position of the animation on screen |
| 2 + 2 | width, height |
| 2 | size of the largest unpacked delta |
| 2 | flags; bit 0 set when a 768-byte palette follows the offset table |
| 4 × (frames + 2) | frame offsets (`u32` little-endian, from the start of the file) |
| 768 | palette, only when flag bit 0 is set |
| | frame data |

Offset *k* is where frame *k* starts; its data runs to the next non-zero
offset. An offset of 0 means the frame stores no data and leaves the picture
as it was. The offset after the last frame marks where the last frame ends;
the final offset is 0, or, for a looping animation, the end of one more delta
(the loop frame) that turns the last picture back into the first.

Each frame is a format 40 XOR delta packed with LCW (format 80). The picture
starts as all zero bytes and every frame's delta is applied to the previous
picture.

## Output

| field | meaning |
|---|---|
| `frames`, `x`, `y`, `width`, `height` | frame count and position/size from the header |
| `palette` | whether the file carries a palette |
| `loop` | whether the file carries a loop frame |
| `crc32` | one CRC-32 per frame (lower-case hex) of the picture after that frame, one palette index per pixel, rows top to bottom |
| `loop_crc32` | for a looping animation, the CRC-32 of the picture after the loop frame |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `wsakit/wsa.py` – offset table, LCW and XOR delta decoding
- `wsainfo.py` – command line

## Running

    python3 wsainfo.py examples/menu.wsa examples/brief.wsa
    python3 -m unittest discover -s tests
