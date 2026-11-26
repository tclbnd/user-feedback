# k051649

Check for the Konami SCC register snapshots (`.k51`) the audio team saves
from an MSX emulator while re-creating the music of Konami cartridges.
`k051info.py` prints one JSON summary per snapshot with what each of the five
wavetable channels plays.

## File layout

`K051649` and a NUL, a mode byte (bit 0 set when the cartridge runs in SCC+
mode), then the chip's register area exactly as it appears in the memory
window of that mode, from the first waveform byte up to and including the
channel enable register.

## Output

| field | meaning |
|---|---|
| `mode` | `"SCC"` or `"SCC+"` |
| `complete` | `true` when the register area is complete |
| `channels` | five objects, channel 1 first (`null` when incomplete) |

Each channel: `on`, `period`, `freq_hz` (output frequency, three decimals),
`volume` (0–15), `wave_slot` (1–5, the waveform RAM the channel plays),
`wave_min`, `wave_max` and `wave_sum` of its 32 waveform samples, and
`audible` (on, non-zero volume, non-silent waveform).

## Layout

- `k051649/wave.py` – register area and channels
- `k051info.py` – command line

## Running

    python3 k051info.py examples/stage.k51 examples/ending.k51
    python3 -m unittest discover -s tests
