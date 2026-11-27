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

## Frame encoding

Every frame stores absolute screen coordinates for its rectangle; the first
frame anchors the movie's origin, which is subtracted from every later frame.
The right and bottom coordinates are **inclusive**. A frame only repaints its
own rectangle, so pixels outside it keep the previous frame.

After the optional palette comes a method byte. The high bit (`0x80`) marks an
LZ-compressed frame: the payload is first run through the VMD LZSS unpacker
(an unpacked-size `u32`, an XOR-key `u32`, then one flag byte per eight payload
bytes, LSB first — set bit = literal byte, clear bit = a `u16` back reference
whose high nibble is the run length minus 3 and low 12 bits the absolute source
position). The remaining method bits select the row encoding:

| method | meaning |
|---|---|
| 1 | RLE: high bit = literal run of `(n & 0x7F) + 1` bytes; low bit = copy `n + 1` pixels from the previous frame |
| 2 | raw, one byte per pixel |
| 3 | like method 1, plus a solid run introduced by `0xFF`: a count byte (plus one) and one colour byte. In method 1 a `0xFF` byte inside a literal run is ordinary data |

Each frame row is an independent RLE segment (the payload splits evenly across
the rectangle's rows); a row may paint fewer pixels than the rectangle is wide,
in which case the remaining columns retain the previous frame's values.

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
