# qtrlekit

Check for the QuickTime Animation (RLE) clips the team pulls out of old Mac
and Windows game CDs. The extraction tool stores the raw frames of one video
track as a frame sequence (`.qtr`); `qtrleinfo.py` decodes the frames in
order and prints one JSON summary per file.

## File layout

`QTRLESEQ`, width and height (`u16` little-endian each), the track depth in
bits per pixel (1, 2, 4, 8 or 16), the number of frames (`u24`
little-endian), then for each frame its length (`u32` little-endian) and the
frame exactly as stored in the movie. The width is a multiple of the pixel
group the depth writes at once (16 pixels at 1 and 2 bits, 8 at 4 bits, 4 at
8 bits, 1 at 16 bits).

Decoding starts from a picture of value 0. Each frame is decoded on top of
the picture the previous one left. Pixel values are palette indices up to 8
bits and the 16-bit RGB555 value at 16 bits.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `depth` | picture size and depth |
| `frames` | one object per frame: `result` (`"decoded"`; `"unchanged"` when the frame leaves the picture as it was; `"stopped"` when decoding had to stop part way, keeping what was drawn) and `crc32` of the whole picture after that frame (every pixel value as `u16` little-endian, rows top to bottom, lower-case hex) |
| `colours` | distinct pixel values in the picture after the last frame |

## Layout

- `qtrlekit/qtrle.py` – frame decoder
- `qtrleinfo.py` – command line

## Running

    python3 qtrleinfo.py examples/menu8.qtr examples/intro16.qtr examples/mono.qtr
    python3 -m unittest discover -s tests
