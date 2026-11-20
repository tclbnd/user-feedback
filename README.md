# cuesplit

The archive receives CD rips as one or more WAV/FLAC images plus a cue sheet.
`cuesplit` reads the sheet and works out where each track starts and ends in
its image file (in samples at 44.1 kHz) so the splitter can cut tracks and
the catalogue can show titles, performers and ISRCs.

Gaps between tracks are appended to the previous track: a track runs from
its INDEX 01 to the next track's INDEX 01 in the same file. `pregap` is the
length of the gap before INDEX 01 (whether it is in the file or not).

Cue times are `mm:ss:ff` with CD frames of 1/75 s (588 samples at 44.1 kHz).
Every FILE has its own clock starting at 00:00:00, so the tracks of a
second disc are not offset by the first disc. A pregap may start in one
file and its INDEX 01 appear in the next; the track belongs to the file
that holds its INDEX 01, and measuring the gap across files needs the
length of the earlier file. `PREGAP` is generated silence outside the
image; `INDEX 00` marks a gap inside it. `POSTGAP` is reported but does
not move the track boundary. Tracks without their own `PERFORMER` inherit
the album performer, and `ISRC` is passed through.

## Layout

- `cuesplit/sheet.py` – cue sheet parsing
- `cuesplit/tracks.py` – track start, end, length
- `cuesplit/naming.py` – output file names
- `cueinfo.py` – command line

## Running

    python3 cueinfo.py examples/harbour.cue --length "Harbour Lights.wav=105840000"
    python3 -m unittest discover -s tests
