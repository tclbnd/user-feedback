# dockit

Check for the Ensoniq ES5503 DOC snapshots (`.doc`) the audio team saves from the
emulator while re-creating Apple IIgs and Ensoniq Mirage soundtracks. A snapshot
freezes the wavetable synthesiser's registers together with its sound RAM;
`docinfo.py` prints one JSON summary per snapshot.

## File layout

`ES5503DC`, the chip registers `$00`–`$E2` as last written (frequency low,
frequency high, volume, data, wavetable pointer, control and bank/size/resolution
banks of 32 registers each, then the global registers `$E0`–`$E2`), then the
128 KB sound RAM starting at address 0.

## Output

| field | meaning |
|---|---|
| `oscillators` | number of oscillators the chip runs |
| `output_rate` | output sample rate in Hz, two decimals |
| `oscs` | one object per running oscillator, oscillator 1 first |

Each oscillator: `osc`, `halted`, `mode` (`"free"`, `"oneshot"`, `"sync"` or
`"swap"`), `channel`, `volume`, `table_address` and `table_size` (where the
wavetable lies in sound RAM and how many bytes it has), `cycle_hz` (frequency of
one full pass through the wavetable, three decimals), and over one pass through
the table from its start: `stops_at` (table index of the first zero byte, which
halts the oscillator, `null` when there is none), `peak` and `sum` (of the
signed samples read before that) and `complete` (`true` when the table lies
inside the RAM in the file). Chip clock 7159090 Hz.

## Layout

- `dockit/osc.py` – registers, oscillators, wavetables
- `docinfo.py` – command line

## Running

    python3 docinfo.py examples/strings.doc examples/drums.doc
    python3 -m unittest discover -s tests
