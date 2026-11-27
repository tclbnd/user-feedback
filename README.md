# ga20kit

Check for the Irem GA20 sound snapshots (`.ga20`) the audio team saves from the
arcade emulator while re-recording R-Type Leo and In The Hunt effects. A
snapshot freezes the four PCM voices at the moment a sound is started;
`ga20info.py` prints one JSON summary per snapshot.

## File layout

`GA20SNAP`, the 32 chip registers as last written (eight per voice: start low,
start high, end low, end high, rate, volume, control, unused), then the sample
ROM the chip reads from, starting at address 0.

## GA20 playback rules

- **Start / end addresses.** The two register bytes form a little-endian word
  that selects a 16-byte paragraph in sample ROM; the byte address the chip
  reads is `word << 4` (word × 16). The voice plays every ROM byte from the
  start address up to, but not including, the end address.
- **Control / key-on.** The control register is a trigger, not an enable flag:
  any non-zero value starts the voice and zero leaves it stopped.
- **Rate.** The chip ticks at `CLOCK / 4` and advances `256 - rate` ROM bytes
  per tick, so the effective playback rate is
  `CLOCK / (4 × (256 - rate))` Hz (clock 3579545 Hz).
- **Volume.** The register is scaled to the chip's 64-step gain:
  effective volume `= register >> 2` (0–63).
- **Waveform.** Samples are unsigned 8-bit PCM centred at `0x80`; the signed
  value of a byte is `byte - 128` (`0x00` = -128, `0x80` = 0, `0xFF` = +127).

## Output

| field | meaning |
|---|---|
| `rom_size` | bytes of sample ROM in the file |
| `voices_on` | voices whose control register starts playback |
| `voices` | one object per voice, voice 1 first |

Each voice: `voice`, `on`, `start` and `end` (byte addresses into the sample
ROM), `volume` (the effective 0–63 volume the chip applies), `rate_hz`
(samples played per second, two decimals), and for a voice that is on:
`length` (samples played until the sample stops), `peak` and `sum` (of the
signed sample values played) and `terminated` (`true` when the end address is
within the ROM, so the sample stops before running off its end). If the end
address lies past the ROM the scan stops at the end of the file and
`terminated` is `false`.

## Layout

- `ga20kit/voices.py` – registers, voices, sample ROM
- `ga20info.py` – command line

## Running

    python3 ga20info.py examples/blast.ga20 examples/voice.ga20
    python3 -m unittest discover -s tests
