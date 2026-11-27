# ga20kit

Check for the Irem GA20 sound snapshots (`.ga20`) the audio team saves from the
arcade emulator while re-recording R-Type Leo and In The Hunt effects. A
snapshot freezes the four PCM voices at the moment a sound is started;
`ga20info.py` prints one JSON summary per snapshot.

## File layout

`GA20SNAP`, the 32 chip registers as last written (eight per voice: start low,
start high, end low, end high, rate, volume, control, unused), then the sample
ROM the chip reads from, starting at address 0.

## Output

| field | meaning |
|---|---|
| `rom_size` | bytes of sample ROM in the file |
| `voices_on` | voices whose control register starts playback |
| `voices` | one object per voice, voice 1 first |

Each voice: `voice`, `on`, `start` and `end` (ROM addresses), `volume` (the
effective volume the chip applies), `rate_hz` (samples played per second, two
decimals), and for a voice that is on: `length` (samples played until the
sample stops), `peak` and `sum` (of the signed sample values played) and
`terminated` (`true` when the sample stops inside the ROM). Chip clock
3579545 Hz.

## Layout

- `ga20kit/voices.py` – registers, voices, sample ROM
- `ga20info.py` – command line

## Running

    python3 ga20info.py examples/blast.ga20 examples/voice.ga20
    python3 -m unittest discover -s tests
