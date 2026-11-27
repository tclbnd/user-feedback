# bubkit

Check for the Konami Bubble System sound snapshots (`.k5289`) the audio team
saves from the arcade emulator while re-creating Nemesis-era soundtracks. A
snapshot freezes the 005289 wavetable generator, the AY-3-8910 control ports
that pick its waveforms and volumes, and the wave PROM; `bubinfo.py` prints one
JSON summary per snapshot.

## File layout

`K005289S`, then little-endian fields: the last address latched with LD1 and
with LD2 (`u16` each, the CPU address bus value), one byte whose bits 0 and 1
tell whether TG1 and TG2 were asserted after that latch, AY control port A and
port B (`u8` each), the frequency each channel had before the latch (`u16`
each), then the 512-byte wave PROM.

## Output

`prom_complete` and `channels`, one object per channel with `channel`,
`volume` (0–15), `waveform` (0–7), `prom_address` (first PROM byte of the
waveform), `frequency` (the channel's current frequency value), `pending` (the
latched value still waiting for its trigger, `null` when there is none),
`cycle_hz` (one whole 32-step waveform per second, three decimals) and
`wave_min`, `wave_max`, `wave_sum` (of the signed waveform samples, `null` when
the PROM is incomplete).

`output` is the chip's mixed audio from reset (both step counters and
waveform positions 0, the channels at their current frequency, waveform and
volume), one sample per chip clock, first 65536 samples: `rate_hz`,
`samples`, `peak` (largest absolute value), `sum` and `crc32` (of the samples
as signed 16-bit little-endian, lower-case hex). PROM bytes missing from the
file read as 0. Chip clock 3579545 Hz.

## Layout

- `bubkit/voices.py` – latches, control ports, PROM waveforms
- `bubinfo.py` – command line

## Running

    python3 bubinfo.py examples/stage1.k5289 examples/boss.k5289
    python3 -m unittest discover -s tests
