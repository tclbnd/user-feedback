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

## Timing model

Ticks are converted following the Standard MIDI File 1.0 specification:

- **Metrical time** (`division` with bit 15 clear) gives ticks per quarter
  note; the tempo map (`FF 51`) converts ticks to seconds, starting from
  500000 µs per quarter at tick 0 when the file sets no initial tempo.
- **Time-code based time** (`division` with bit 15 set) encodes SMPTE frames
  per second (`-24`, `-25`, `-29` for 29.97 drop-frame, `-30`) and ticks per
  frame; seconds then depend only on the division, not on tempo events.
- Formats 0 and 1 use one tempo map; in format 1 the tempo belongs to the
  conductor track (track 0), and those events time every other track.
  Format 2 tracks are independent sequences, each with its own tempo map.
- Notes are opened by note-on and closed by note-off *or* by a note-on with
  velocity 0, per channel and pitch. Running status, multi-byte deltas and
  multi-byte meta lengths are handled. A note still open at the end of its
  track is closed at the track's last event.

Lyrics are timed at the moment their lyric event occurs, using the same
tempo map as the melody, and are listed in time order.

## Layout

- `midiclock/smf.py` – chunks and track events
- `midiclock/timing.py` – tempo map, notes, lyrics in seconds
- `midiclock/scoring.py` – singing score
- `midiinfo.py` – command line

## Running

    python3 midiinfo.py examples/harbour.mid
    python3 -m unittest discover -s tests
