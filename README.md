# smckit

Check for the Apple Graphics (SMC) animations the team pulls out of
QuickTime movies on old Mac game CDs. The extraction tool stores the raw
frames of one 8-bit video track as a frame sequence (`.smc`); `smcinfo.py`
decodes the frames in order into palette indices and prints one JSON
summary per file. The palette itself is not needed for the check.

## File layout

`SMCSEQ` and two NULs, width and height (`u16` little-endian each, both
multiples of 4), the number of frames (`u32` little-endian), then for each
frame its length (`u32` little-endian) and the frame exactly as stored in the
movie, chunk header included.

Decoding starts from a picture of index 0. Each frame is decoded on top of
the picture the previous one left. A frame that cannot be decoded is
reported with `ok` false; whatever it had already drawn stays.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, and `crc32` of the whole picture after that frame (one byte per pixel, rows top to bottom, lower-case hex) |
| `colours` | distinct palette indices in the picture after the last frame |
| `top` | up to four `[index, pixels]` pairs for the most used indices in that picture, most used first, ties by index |

## Layout

- `smckit/smc.py` – frame decoder
- `smcinfo.py` – command line

## Running

    python3 smcinfo.py examples/logo.smc examples/menu.smc
    python3 -m unittest discover -s tests
