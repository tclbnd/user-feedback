# ltckit

Sync helper for the location sound and camera recordings: it reads the SMPTE
linear timecode (LTC) recorded as audio in a WAV file. `ltcread.py` prints one
JSON summary per file, used to line up takes in the edit.

## Input

PCM WAV files (8, 16 or 24 bit, plain or WAVE_FORMAT_EXTENSIBLE). In files with
more than one channel the LTC is on the last channel, as cameras and recorders
put it there.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `channels` | from the WAV header |
| `fps` | timecode rate: `24`, `25`, `30`, or `30000/1001` for drop-frame timecode |
| `drop_frame` | the timecode is drop-frame |
| `reverse` | the LTC was recorded or played backwards |
| `frames` | complete LTC frames in the file (a frame cut off at the start or end of the recording does not count) |
| `first`, `last` | first and last complete frames in recording order, `HH:MM:SS:FF` (`;` before the frames for drop-frame) |
| `user_bits` | the 32 user bits of the first frame as 8 hex digits, binary group 8 first and group 1 last |
| `color_frame` | the colour frame flag of the first frame |
| `user_bits_format` | how the user bits are used, from the binary group flags BGF2 and BGF0 of the first frame: `unspecified` (both 0), `iso_8bit` (BGF0 only), `date_timezone` (BGF2 only), `page_line` (both) |
| `gaps` | places where a complete frame is not the one right after the frame before it in recording order (one frame earlier when the LTC runs backwards), counting drop-frame timecode by its own rule |

## Layout

- `ltckit/ltc.py` – WAV reading, bit recovery, frame decoding
- `ltcread.py` – command line

## Running

    python3 ltcread.py examples/scene12_take3.wav examples/camera_b.wav
    python3 -m unittest discover -s tests
