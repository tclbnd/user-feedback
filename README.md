# midiclock

The karaoke catalogue ships songs as Standard MIDI Files. The lyric timer
turns a file into wall-clock times: the tempo map, every melody note with
its start and end in seconds, and the time each lyric syllable is shown.
The singing score compares a singer's pitch track against those notes.

## Output

`midiinfo.py` prints one JSON object:

| field | meaning |
|---|---|
| `format`, `division`, `tracks` | from the header chunk (`division` as stored) |
| `tempo_map` | tempo changes in tick order: `tick`, `us_per_quarter`, `seconds` |
| `notes` | `track`, `channel`, `pitch`, `velocity`, `start`, `end`, sorted by start time, then track, then pitch |
| `lyrics` | lyric events: `seconds` (time of the event), `text` |

All times are seconds rounded to 6 decimals.

## Layout

- `midiclock/smf.py` – chunks and track events
- `midiclock/timing.py` – tempo map, notes, lyrics in seconds
- `midiclock/scoring.py` – singing score
- `midiinfo.py` – command line

## Running

    python3 midiinfo.py examples/harbour.mid
    python3 -m unittest discover -s tests
