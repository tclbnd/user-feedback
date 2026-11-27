# x1kit

Check for the Seta X1-010 sound snapshots (`.x1`) the audio team saves from the
arcade emulator while re-creating Seta and Allumer soundtracks. A snapshot
freezes the chip's 8 KB RAM together with the PCM ROM; `x1info.py` prints one
JSON summary per snapshot.

## File layout

`SETAX1SN`, the 8 KB chip RAM as last written (channel registers at
`$0000`–`$007F`, eight per channel; envelope tables and waveform tables in the
rest of the RAM), then the PCM sample ROM starting at address 0.

## Output

`output_rate` (chip output rate in Hz), `channels_on` and `channels`, one object
per keyed channel with `channel` and `mode` (`"pcm"` or `"wave"`).

A PCM channel adds `start` and `end` (ROM byte addresses, `end` exclusive),
`left`, `right` (0–15 channel volumes), `rate_hz` (sample bytes played per
second, two decimals) and, over the whole sample, `length`, `peak`, `sum` (of
the signed samples) and `complete` (`true` when the sample lies inside the
ROM).

A waveform channel adds `waveform` and `wave_address`, `cycle_hz` (one whole
waveform per second, three decimals), `envelope` and `env_address`, `once`
(`true` when the envelope plays once instead of repeating), `env_hz` (one whole
envelope per second, three decimals), `left` and `right` (the volumes of the
first envelope step) and `wave_peak`, `wave_sum` (of the signed waveform).
Chip clock 16000000 Hz.

## Layout

- `x1kit/chans.py` – channels, waveforms, envelopes, PCM ROM
- `x1info.py` – command line

## Running

    python3 x1info.py examples/thunder.x1 examples/organ.x1
    python3 -m unittest discover -s tests
