# c140kit

Check for the Namco C140 sound snapshots (`.c140`) the audio team saves from the
arcade emulator while re-recording System 2 effects and music. A snapshot
freezes the 24 voices at the moment their key-on is written; `c140info.py`
prints one JSON summary per snapshot.

## File layout

`C140SNAP`, the 24 voice register blocks (16 bytes each, voice 1 first) as last
written, then the sample ROM.

Each register block holds, in byte order: the right and left 8-bit channel
volumes, then big-endian 16-bit words for frequency, the control/bank word,
start, end and loop. The control word's top byte is the bank select (0–3) and
its low byte carries the flags: key-on (`0x80`), compressed format (`0x08`)
and loop enable (`0x10`).

The ROM follows the chip's banked addressing: each bank is 65536 16-bit words,
so a voice's word address is `bank * 65536 + start`. Every ROM word is stored
big-endian. Linear samples are signed 12-bit values in the low 12 bits of a
word; compressed samples are the 8-bit companded code in the high byte (the low
byte is unused). A voice's playback rate is `21390 * frequency / 32768` Hz.

## Output

| field | meaning |
|---|---|
| `rom_words` | 16-bit words of sample ROM in the file |
| `voices_keyed` | voices keyed on |
| `voices` | one object per keyed voice, lowest voice first |

Each voice: `voice`, `address` and `loop_start` (word addresses in the ROM,
`loop_start` `null` for a voice that does not loop), `length` (words from start
to end), `format` (`"linear"` or `"compressed"`), `left` and `right` (the
voice's channel volumes), `rate_hz` (sample words stepped per second, two
decimals), and over one pass from start to end: `peak` (largest absolute
sample value) and `sum` (of the sample values), as 12-bit signed numbers, and
`complete` (`true` when the whole pass is inside the ROM). Sample clock
21390 Hz.

## Layout

- `c140kit/voices.py` – registers, banking, sample decoding
- `c140info.py` – command line

## Running

    python3 c140info.py examples/engine.c140 examples/voice.c140
    python3 -m unittest discover -s tests
