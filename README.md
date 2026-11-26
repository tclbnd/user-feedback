# fdskit

Check for the Famicom Disk System sound snapshots (`.fdsa`) the audio team saves
from the emulator while re-creating disk-game soundtracks. A snapshot freezes
the FDS wavetable channel at one moment; `fdsinfo.py` prints one JSON summary
per snapshot.

## File layout

`FDSAUDIO`, the eleven registers `$4080`–`$408A` as last written, the current
volume gain (as read from `$4090`) and modulator gain (as read from `$4092`),
then the 64 bytes of wave RAM `$4040`–`$407F` and the 32 entries of the
modulation table, one byte each, as last written through `$4088`.

## Output

| field | meaning |
|---|---|
| `pitch` | the wave pitch register value |
| `halted`, `envelopes_off`, `wave_write` | the halt and envelope-disable bits of `$4083`, the write-enable bit of `$4089` |
| `mod_counter` | the modulator counter as a signed number |
| `freq_hz` | frequency of one whole 64-step wave cycle with the current modulation applied, three decimals; `null` while halted |
| `mod_freq_hz` | frequency of one whole pass through the modulation table, three decimals |
| `level` | output level as an exact fraction of full scale (current volume gain and master volume) |
| `wave_min`, `wave_max`, `wave_sum` | over the 64 wave samples |
| `mod_table` | the 32 modulation entries as counter increments, `"reset"` for an entry that resets the counter |
| `complete` | `true` when the wave RAM and the whole modulation table are in the file |

CPU clock 1789773 Hz. Wave and table fields are `null` when the snapshot is
incomplete.

## Layout

- `fdskit/regs.py` – registers, modulation, volume, wavetable
- `fdsinfo.py` – command line

## Running

    python3 fdsinfo.py examples/flute.fdsa examples/bass.fdsa
    python3 -m unittest discover -s tests
