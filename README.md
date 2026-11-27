# kdsckit

Check for the Konami K053260 sound snapshots (`.kdsc`) the audio team saves from
the arcade emulator while re-recording TMNT, Simpsons and Vendetta effects. A
snapshot freezes the four voices at the moment their key-on is written;
`kdscinfo.py` prints one JSON summary per snapshot.

## File layout

`K053260S`, the 48 chip registers `$00`–`$2F` as last written, then the
sample ROM the chip reads from, starting at address 0.

## Output

| field | meaning |
|---|---|
| `rom_size` | bytes of sample ROM in the file |
| `voices_on` | voices keyed on |
| `voices` | one object per voice, voice 1 first |

Each voice: `voice`, `on`, `start` and `length` (as programmed), `rate_hz`
(samples stepped per second, two decimals), `volume`, `left` and `right` (the
volume after panning, as the chip scales it), `loop`, `reverse`, `format`
(`"pcm"` or `"kadpcm"`), and for a voice that is on, over one pass through the
sample: `samples` (output samples), `peak` (largest absolute output),
`sum`, `last` (last output sample) and `complete` (`true` when every byte of
the pass is inside the ROM). Outputs are the signed 8-bit values the voice
produces. Chip clock 3579545 Hz.

## Layout

- `kdsckit/voices.py` – registers, voices, sample decoding
- `kdscinfo.py` – command line

## Running

    python3 kdscinfo.py examples/shell.kdsc examples/theme.kdsc
    python3 -m unittest discover -s tests
