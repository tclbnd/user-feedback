# vttkit

Subtitling vendors deliver WebVTT for every title. Ingest parses the file
into a cue list (times in milliseconds, cue settings, the speaker of `<v>`
spans and the plain text a screen reader would get), runs quality checks
and exports SubRip for legacy players.

## Layout

- `vttkit/parser.py` – WebVTT parsing
- `vttkit/srt.py` – SubRip export
- `vttkit/qc.py` – vendor quality report
- `vttcheck.py` – command line

## Running

    python3 vttcheck.py examples/ep01.vtt
    python3 vttcheck.py --srt examples/ep01.vtt
    python3 -m unittest discover -s tests
