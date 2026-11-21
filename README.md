# cuesplit

The archive receives CD rips as one or more WAV/FLAC images plus a cue sheet.
`cuesplit` reads the sheet and works out where each track starts and ends in
its image file (in samples at 44.1 kHz) so the splitter can cut tracks and
the catalogue can show titles, performers and ISRCs.

Gaps between tracks are appended to the previous track: a track runs from
its INDEX 01 to the next track's INDEX 01 in the same file. `pregap` is the
length of the gap before INDEX 01 (whether it is in the file or not).

## Cue sheets

Sheets follow the CDRWIN format, which is what rippers write:

- A time is `mm:ss:ff` with 75 CD frames per second, so one frame is 588
  samples at 44.1 kHz. Seconds must be 0–59 and frames 0–74; a time that is
  malformed or out of range is an error.
- String values are quoted with double quotes and may contain spaces; `""`
  inside a quoted string is a literal quote. Bare values are taken to the end
  of the line.
- `FILE "name" WAVE` opens an image and `TRACK nn AUDIO` starts a track in
  it. Times are relative to the file the command appears in — each image of
  a multi-disc set has its own timeline starting at zero.
- `INDEX 00` marks the start of the gap before a track and `INDEX 01` its
  audio; `PREGAP` declares a gap that is not in the image. A track without
  `PERFORMER` takes the album's performer.
- A track may span two images: its `INDEX 00` closes one file and its
  `INDEX 01` opens the next, with or without another `TRACK` command. The
  track belongs to the file of its `INDEX 01`, and its pregap includes the
  rest of the previous image when that file's length is known.

## Output

`cueinfo.py SHEET [--length FILE=SAMPLES ...]` prints one JSON object with the
album `title` and `performer` and a `tracks` list. Each track has `number`,
`title`, `performer`, `file`, `start` and `end` (samples within its file),
`length`, `pregap` (samples), `isrc` and `type`. `end` is the next track's
INDEX 01 in the same file, or the file length given with `--length` for the
last track of a file; without a length it is `null`, and so is `length`.

A sheet that cannot be laid out (a bad time, a track without `INDEX 01`, an
`INDEX 01` before its `INDEX 00`, or a track starting past the end of its
file) prints an `error` object instead and exits non-zero.

## Layout

- `cuesplit/sheet.py` – cue sheet parsing
- `cuesplit/tracks.py` – track start, end, length
- `cuesplit/naming.py` – output file names
- `cueinfo.py` – command line

## Running

    python3 cueinfo.py examples/harbour.cue --length "Harbour Lights.wav=105840000"
    python3 -m unittest discover -s tests
