# muskit

Catalogue check for the soundtrack lumps extracted from the id Tech 1 games
(DMX `.mus` music). `musinfo.py` prints one JSON summary per lump, used to
check running time, channels and events before conversion to MIDI.

## Output

| field | meaning |
|---|---|
| `primary_channels`, `secondary_channels` | from the header (integers) |
| `instruments` | the instrument patch numbers the header lists (integers) |
| `ticks`, `duration` | the total of all delays, and the same in seconds as a fraction string |
| `notes` | the number of play-note events |
| `channels` | MUS channels that play notes, sorted (integers 0–15) |
| `percussion` | `true` when the percussion channel plays notes |
| `controllers` | change-controller events counted by name: `program`, `bank`, `modulation`, `volume`, `pan`, `expression`, `reverb`, `chorus`, `sustain`, `soft`, else `controller_N` |
| `system_events` | system events counted by name: `sounds_off`, `notes_off`, `mono`, `poly`, `reset`, else `system_N` |
| `pitch_bends` | the number of pitch wheel events |
| `score_end` | `true` when a score-end event ends the score |

Only the score bytes the header gives belong to the music. Event encoding,
delays and timing follow the DMX MUS format as id Tech 1 engines play it.

## Layout

- `muskit/mus.py` – header, events, timing
- `musinfo.py` – command line

## Running

    python3 musinfo.py examples/e1m1.mus examples/intermission.mus
    python3 -m unittest discover -s tests
