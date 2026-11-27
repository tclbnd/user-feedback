# kmvckit

Check for the Karl Morton's Video Codec (KMVC) cut-scenes the team pulls out
of AVI files on mid-90s DOS game CDs. The extraction tool stores the raw
frames of one video stream as a frame sequence (`.kmv`); `kmvcinfo.py`
decodes the frames in order through the stream's palette and prints one JSON
summary per file.

## File layout

`KMVCSEQ` and a NUL, width and height (`u16` little-endian each, at most
320 × 200), the palette size from the stream's codec header (`u16`
little-endian, below 255), the number of frames (`u16` little-endian), then
for each frame its length (`u32` little-endian) and the frame exactly as
stored in the AVI.

Before the first frame the palette is a grey ramp (entry i is i, i, i) and
both picture buffers are index 0. A frame the decoder rejects is reported
with `ok` false and changes nothing.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, `key` (key frame flag) and `crc32` of the decoded picture as RGB24 through the palette in force after that frame (rows top to bottom, lower-case hex) |

## Layout

- `kmvckit/kmvc.py` – frame decoder
- `kmvcinfo.py` – command line

## Running

    python3 kmvcinfo.py examples/intro.kmv examples/fade.kmv
    python3 -m unittest discover -s tests
