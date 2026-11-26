# opllkit

Check for the YM2413 register snapshots (`.opll`) the audio team saves from an
emulator while re-creating MSX-MUSIC and Master System FM soundtracks. A
snapshot holds the values last written to the chip's registers;
`opllinfo.py` prints one JSON summary per snapshot with what the chip plays
at that moment.

## File layout

`YM2413` and two NULs, then 64 bytes: the values of registers `0x00`–`0x3F`
(addresses the chip does not have are zero).

## Output

| field | meaning |
|---|---|
| `complete` | `true` when all 64 register values are present |
| `rhythm_mode` | the rhythm section is enabled |
| `melodic` | one object per melody channel, channel 1 first |
| `drums` | rhythm mode only, otherwise `null`: an object keyed `"bass drum"`, `"snare drum"`, `"tom-tom"`, `"top cymbal"`, `"hi-hat"`, each `{"on": …, "volume_db": …}` |

Each melody channel: `channel`, `key_on`, `sustain`, `instrument` (`"custom"`
or the built-in instrument's name: `"violin"`, `"guitar"`, `"piano"`,
`"flute"`, `"clarinet"`, `"oboe"`, `"trumpet"`, `"organ"`, `"horn"`,
`"synthesizer"`, `"harpsichord"`, `"vibraphone"`, `"synth bass"`,
`"acoustic bass"`, `"electric guitar"`), `volume_db` (level relative to full
volume, e.g. `-6.0`), `fnum`, `block` and `freq_hz` (three decimals).

## Layout

- `opllkit/regs.py` – registers, channels, rhythm
- `opllinfo.py` – command line

## Running

    python3 opllinfo.py examples/stage.opll examples/drums.opll
    python3 -m unittest discover -s tests
