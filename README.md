# cmvkit

Frame check for the CMV cut-scenes of Electronic Arts' mid-90s games. The rip
tool stores the video packets of one movie as a `.cmv` pack; `cmvinfo.py`
decodes every frame the way the game's player does and prints one JSON
summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `CMVMOVIE` |
| 2 | packet count (`u16` little-endian) |
| | for each packet its size (`u32` little-endian) and the packet as stored in the movie |

A packet may start with an `MVIh` header chunk (tag, `u32` chunk size, then
width and height, frame rate, and a range of 8-bit red, green, blue palette
entries given by first entry and count); after it come an 8-byte preamble, a
sub-type byte (bit 0 set for a motion frame), one unused byte and the frame
data. Key frames store the picture row by row. Motion frames code 4 × 4
blocks with a block map and a stream of raw blocks and vectors; blocks refer
to the previous frame or to the one before it. The palette starts black and
keeps its entries until a header changes them.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size from the last header |
| `frames` | one object per packet: `ok`, `key`, and `crc32` of the decoded frame as RGB bytes (three per pixel, rows top to bottom), lower-case hex; a packet the player rejects gives `ok` false and the other fields `null` |

A file that cannot be read at all gets an `error` entry instead.

## Layout

- `cmvkit/cmv.py` – header and frame decoder
- `cmvinfo.py` – command line

## Running

    python3 cmvinfo.py examples/intro.cmv examples/race.cmv
    python3 -m unittest discover -s tests
