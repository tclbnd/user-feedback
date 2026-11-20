# dolbyscan

Broadcast deliveries include AC-3 or E-AC-3 audio as elementary streams.
Before acceptance we summarise each stream (codec, sample rate, channel
layout with LFE, bit rate, dialogue normalisation, frame and sample counts,
duration) and compare it with the delivery spec. `channels` counts the LFE
channel; `dialnorm` is in dB (e.g. -24).

Parsing follows ATSC A/52: AC-3 frame sizes for all three sample rates
(44.1 kHz frames alternate between two sizes), the bsi fields that appear
with the channel mode (cmixlev/surmixlev/dsurmod) ahead of LFE and
dialnorm, and E-AC-3 (Annex E) frame headers with reduced sample rates
and variable blocks per frame. An E-AC-3 programme is its independent
substream plus dependent substreams; dependent substreams only add
channels, while bit rate, sample count and duration are per programme.
Corrupt bytes mid-stream and an incomplete final frame are skipped; the
complete frames are still counted.

## Layout

- `dolbyscan/syncframe.py` – sync frame headers
- `dolbyscan/stream.py` – stream summary
- `dolbyscan/compliance.py` – delivery spec checks
- `dolbyinfo.py` – command line

## Running

    python3 dolbyinfo.py examples/*.ac3 examples/*.ec3
    python3 -m unittest discover -s tests
