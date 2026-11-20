# aacpack

The radio capture boxes record each station as raw ADTS files. Before a file
goes into the archive we summarise it (stream parameters, sample count,
duration) and produce the AudioSpecificConfig and payload size the MP4
muxer needs to repackage it without re-encoding.

## Layout

- `aacpack/adts.py` – frame headers
- `aacpack/probe.py` – stream summary and decoder config
- `aacpack/schedule.py` – archive cut list
- `aacinfo.py` – command line

## Running

    python3 aacinfo.py examples/*.aac
    python3 -m unittest discover -s tests
