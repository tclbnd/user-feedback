# midiclock

The karaoke catalogue ships songs as Standard MIDI Files. The lyric timer
turns a file into wall-clock times: the tempo map, every melody note with
its start and end in seconds, and the time each lyric syllable is shown.
The singing score compares a singer's pitch track against those notes.

## Layout

- `midiclock/smf.py` – chunks and track events
- `midiclock/timing.py` – tempo map, notes, lyrics in seconds
- `midiclock/scoring.py` – singing score
- `midiinfo.py` – command line

## Running

    python3 midiinfo.py examples/harbour.mid
    python3 -m unittest discover -s tests
