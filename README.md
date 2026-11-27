# vmdkit

Check for the Sierra VMD cut-scenes (Phantasmagoria, King's Quest VII and
other Sierra titles) that the team re-masters. The extraction tool stores the
video frame records of one VMD file in order (`.vmf`); `vmdinfo.py` decodes
them through the movie's palette and prints one JSON summary per file.

## File layout

`VMDFRAME`, the picture width and height (`u16` little-endian each), the
size of the unpack buffer from the VMD header (`u32` little-endian, 0 when
the movie has none), the number of frames (`u16` little-endian), then for
each frame its length (`u32` little-endian) and the frame record exactly as
stored in the VMD: the 16-byte frame header, an optional palette, then the
frame data.

The picture starts as index 0 with an all-black palette. A frame that cannot
be decoded is reported with `ok` false and does not change the picture.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per frame: `ok`, and `crc32` of the picture after it as RGB24 through the current palette (rows top to bottom, lower-case hex), `null` when not decoded |
| `colours` | distinct palette indices in the picture after the last frame |

## Layout

- `vmdkit/vmd.py` – frame decoder
- `vmdinfo.py` – command line

## Running

    python3 vmdinfo.py examples/castle.vmf examples/portrait.vmf
    python3 -m unittest discover -s tests
