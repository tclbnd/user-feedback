# ymkit

Catalogue check for the Atari ST and Amstrad chip-music collection, kept as
YM register dumps (`.ym`: YM3, YM3b, YM5 and YM6). `yminfo.py` prints one JSON
summary per tune, used to check lengths, loop points and register data.

## Output

| field | meaning |
|---|---|
| `format` | `YM3!`, `YM3b`, `YM5!` or `YM6!` |
| `frames` | the number of register frames (an integer) |
| `clock`, `rate` | the sound chip clock in Hz and the player rate in frames per second (integers; YM3 files are 2000000 and 50) |
| `loop_frame` | the frame the tune loops back to (an integer, 0 when the file gives none) |
| `interleaved` | `true` when the register data is stored register by register |
| `digidrums`, `digidrum_bytes` | the number of digidrum samples and their total size in bytes |
| `title`, `author`, `comment` | the header strings (Latin-1), `null` when empty or for YM3 |
| `end_marker` | `true` when `End!` follows the register data (YM5/YM6), `null` for YM3 |
| `duration`, `loop_seconds` | `frames / rate` and `loop_frame / rate` in seconds as fraction strings |
| `envelope_writes` | the number of frames that write the envelope shape register 13 (YM5/YM6 mark "no write" with `0xFF`; every YM3 frame writes it) |
| `tone_frames` | per channel A, B, C, the number of frames whose mixer register 7 enables the tone |
| `first_tone_a` | channel A's tone frequency in the first frame (`clock / (16 × period)`, the 12-bit period from registers 0 and 1) as a fraction string, `null` when the period is 0 |

YM5 and YM6 headers are big-endian; YM3 frames have 14 registers, YM5/YM6
frames 16. YM3b ends with its loop frame as a little-endian 32-bit number;
a `YM3!` file is reported as `YM3b` when such a trailing loop frame is
present and as `YM3!` otherwise. Header layout, digidrums, strings and the
register order follow the YM format as written by Leonard's converters:
each digidrum sample is a big-endian 32-bit size followed by its bytes, the
samples sit between the fixed header and the three strings, and the register
data then begins. Interleaved dumps (bit 0 of the attributes) store register
0 for all frames, then register 1 for all frames, and so on.

## Layout

- `ymkit/ym.py` – header, register data
- `yminfo.py` – command line

## Running

    python3 yminfo.py examples/wings.ym examples/cybernoid.ym
    python3 -m unittest discover -s tests
