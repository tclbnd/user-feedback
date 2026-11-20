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

## What the reader handles

Parsing follows Standard MIDI File 1.0:

- format 0, 1 and 2 files, `.mid` and RIFF-wrapped `.rmi`
- variable length quantities up to four bytes (long deltas included)
- running status, also kept in force across interleaved lyric meta events
  (the spec cancels it there; the catalogue's files do not)
- meta events with payloads of any length, `F0` and `F7` sysex
- note-on with velocity 0 as note-off, and note-off in general; notes
  still sounding at the end of a track are closed there
- PPQ and SMPTE timebases (-24, -25, -29.97 drop-frame, -30)
- one global tempo map for format 0 and 1 (tempo events may sit in any
  track, usually track 0); format 2 tracks get independent tempo maps
- chunks that are not `MTrk` are skipped by their declared length

Times are computed with exact fractions and only rounded to microseconds
in the output, so long songs do not drift.

## Running

    python3 midiinfo.py examples/harbour.mid
    python3 -m unittest discover -s tests
