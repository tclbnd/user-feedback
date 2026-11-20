# dolbyscan

Broadcast deliveries include AC-3 or E-AC-3 audio as elementary streams.
Before acceptance we summarise each stream (codec, sample rate, channel
layout with LFE, bit rate, dialogue normalisation, frame and sample counts,
duration) and compare it with the delivery spec. `channels` counts the LFE
channel; `dialnorm` is in dB (e.g. -24).

## Layout

- `dolbyscan/syncframe.py` – sync frame headers
- `dolbyscan/stream.py` – stream summary
- `dolbyscan/compliance.py` – delivery spec checks
- `dolbyinfo.py` – command line

## Running

    python3 dolbyinfo.py examples/*.ac3 examples/*.ec3
    python3 -m unittest discover -s tests
