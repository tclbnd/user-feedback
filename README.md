# cinkit

Check for the id Software cinematic files (`.cin`, the Quake II cut-scene
format) the team re-encodes. `cininfo.py` reads each file, decodes every
video frame with the file's Huffman tables and current palette, collects the
interleaved audio, and prints one JSON summary per file.

## File layout

The file exactly as shipped: the five little-endian `u32` header values
(width, height, audio sample rate, bytes per audio sample, audio channels;
a sample rate of 0 means no audio), the 64 KB of Huffman tables, then the
chunks to the end of the file or an end-of-file command.

The picture starts black and every frame is a whole picture. A frame that
cannot be decoded is reported with `crc32` and `colours` `null`.

## Output

| field | meaning |
|---|---|
| `width`, `height` | picture size |
| `frames` | one object per video frame: `palette` (whether a new palette arrived with it), `crc32` of the frame as RGB24 (8 bits per channel, rows top to bottom, lower-case hex) and `colours` (distinct palette indices in the frame) |
| `ended_by` | `"end"` for an end-of-file command, `"eof"` when the data simply ran out |
| `audio` | `null` without audio, otherwise `rate`, `channels`, `bits`, `samples` (per channel), `peak` and `crc32` of the audio as signed 16-bit little-endian samples (8-bit audio widened by 256) |

## Layout

- `cinkit/idcin.py` – chunk reader, Huffman trees and frame decoder
- `cininfo.py` – command line

## Running

    python3 cininfo.py examples/intro.cin examples/silent.cin
    python3 -m unittest discover -s tests
