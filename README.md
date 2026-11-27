# seqkit

Check for the cut-scenes of Tiertex games (Flashback CD and others) whose
video is stored as Tiertex SEQ. The extraction tool writes the video part of
each frame to a frame file (`.tsq`); `seqinfo.py` decodes the frames in
order into the 256 × 128 palette picture and prints one JSON summary per
file.

## File layout

`TSEQVID` and a NUL, the frame count (`u32` little-endian), then for each
frame its length (`u32` little-endian) and the video data of the frame
exactly as stored in the SEQ file, starting with its flags byte.

Decoding starts from a picture of index 0 and an all-black palette. Each
frame is decoded on top of the previous picture. A frame that cannot be
decoded is reported with `ok` false; whatever it had already drawn stays.

## Output

| field | meaning |
|---|---|
| `frames` | one object per frame: `ok`, and `crc32` of the 256 × 128 index picture after it (rows top to bottom, lower-case hex) |
| `colours` | distinct palette indices in the last picture |
| `rgb_crc32` | CRC-32 of the last picture with the current palette applied (R G B per pixel, 8 bits each) |

## Layout

- `seqkit/tiertex.py` – frame decoder
- `seqinfo.py` – command line

## Running

    python3 seqinfo.py examples/intro.tsq examples/lift.tsq
    python3 -m unittest discover -s tests
