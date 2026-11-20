# ivfscan

The encoder lab stores AV1 test encodes in IVF. The dashboard needs, per
clip: the IVF header fields, the time base (`timebase` is seconds per tick,
written as a fraction), how many frames (temporal units) the file really
holds, first and last pts, the number of key frames, how many OBUs of each
type it contains and the main sequence header fields.

## Layout

- `ivfscan/ivf.py` – IVF container
- `ivfscan/obu.py` – OBUs of a temporal unit
- `ivfscan/seqhdr.py` – sequence header fields
- `ivfscan/summary.py` – clip summary
- `ivfscan/bdrate.py` – rate curves
- `ivfinfo.py` – command line

## Running

    python3 ivfinfo.py examples/*.ivf
    python3 -m unittest discover -s tests
