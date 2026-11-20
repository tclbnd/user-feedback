# oggscan

Voice notes in the messaging app are Ogg Opus files recorded on phones and
web clients. The service shows each note's length in the chat bubble, bills
transcription by the second and drops files that fail integrity checks.

## Layout

- `oggscan/ogg.py` – pages and packets
- `oggscan/opus.py` – OpusHead and packet TOC
- `oggscan/probe.py` – duration and stream figures
- `oggscan/retention.py` – inbox retention
- `opusinfo.py` – command line

## Running

    python3 opusinfo.py examples/*.opus
    python3 -m unittest discover -s tests
