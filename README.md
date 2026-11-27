# rpzakit

Check for the Apple Video ("Road Pizza", RPZA) clips the team pulls out of
QuickTime movies on old Mac game CDs. The extraction tool stores the raw
frames of one video track as a frame sequence (`.rpz`); `rpzainfo.py`
decodes the frames in order and prints one JSON summary per file.

## File layout

`RPZASEQ` and a NUL, width and height (`u16` little-endian each; the height
is a multiple of 4), the number of frames (`u32` little-endian), then for
each frame its length (`u32` little-endian) and the frame exactly as stored
in the movie, chunk header included.

Decoding starts from a picture of colour 0. Each frame is decoded on top of
the picture the previous one left. A frame that cannot be decoded is
reported with `ok` false; whatever it had already drawn stays.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, and `crc32` of the whole picture after that frame (RGB555 values as `u16` little-endian, rows top to bottom, lower-case hex) |
| `colours` | distinct RGB555 values in the picture after the last frame |
| `mean_rgb` | mean of the 5-bit red, green and blue components of that picture, two decimals |

## Layout

- `rpzakit/rpza.py` – frame decoder
- `rpzainfo.py` – command line

## Running

    python3 rpzainfo.py examples/title.rpz examples/door.rpz
    python3 -m unittest discover -s tests
