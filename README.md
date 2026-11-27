# vidkit

Check for the VID movies of Bethesda's early games (The Elder Scrolls:
Arena intro, Terminator: Future Shock and others). `vidinfo.py` walks the
blocks of each `.vid` file as the game's player does, decodes the video
frames into the palette picture and gathers the 8-bit sound, and prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `width`, `height`, `frames_declared` | picture size and frame count from the file header |
| `frames` | one object per video frame in file order: `type` (`"I"` key frame, `"P"` update, `"Y"` update starting at a given line), `ticks` (how long the frame stays, in the player's ticks, header delay included) and `crc32` of the palette-index picture after it (rows top to bottom, lower-case hex) |
| `palettes` | palette blocks met |
| `rgb_crc32` | CRC-32 of the last picture with the palette in force for it (R G B per pixel, 8 bits each) |
| `audio` | `null` without sound, otherwise `rate` (Hz), `samples`, `peak` (largest distance from 128) and `crc32` of the unsigned 8-bit samples |
| `ended` | `"eof"` (end block), `"data"` (file ended without one) or `"error"` (bad or truncated block; everything before it is reported) |

The picture starts as index 0 and the palette as all black.

## Decoding rules (matching the game player)

- A video block ends on an explicit zero code, or as soon as the whole
  picture (everything below the start line for a `Y` frame) is covered; the
  last block of a movie often has no zero code.
- A code below 0x80 is a literal run of that many palette indices (runs may
  cross row boundaries). A code with the high bit set on an `I` frame is an
  RLE fill of one colour, clipped to the end of its row so it never paints
  into the next row. On a `P`/`Y` frame a high-bit code is a skip: those
  destination pixels keep whatever an earlier frame left there.
- Palette components are 6-bit VGA DAC values; each is expanded to 8 bits by
  copying its top two bits into the low bits, so 63 maps to 255.
- The first audio block carries a three-byte big-endian 1&nbsp;MHz timer
  divisor; the rate is `1000000 // divisor`.
- A frame's duration is its block ticks plus the header delay.

## Layout

- `vidkit/bethsoft.py` – block walker and frame decoder
- `vidinfo.py` – command line

## Running

    python3 vidinfo.py examples/arena.vid examples/future.vid
    python3 -m unittest discover -s tests
