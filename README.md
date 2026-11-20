# cuesplit

The archive receives CD rips as one or more WAV/FLAC images plus a cue sheet.
`cuesplit` reads the sheet and works out where each track starts and ends in
its image file (in samples at 44.1 kHz) so the splitter can cut tracks and
the catalogue can show titles, performers and ISRCs.

Gaps between tracks are appended to the previous track: a track runs from
its INDEX 01 to the next track's INDEX 01 in the same file. `pregap` is the
length of the gap before INDEX 01 (whether it is in the file or not).

## Layout

- `cuesplit/sheet.py` – cue sheet parsing
- `cuesplit/tracks.py` – track start, end, length
- `cuesplit/naming.py` – output file names
- `cueinfo.py` – command line

## Running

    python3 cueinfo.py examples/harbour.cue --length "Harbour Lights.wav=105840000"
    python3 -m unittest discover -s tests
