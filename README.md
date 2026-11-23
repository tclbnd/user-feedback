# gpmfkit

Telemetry check for the action-camera footage library: the GoPro metadata
track (GPMF) is extracted from each clip as a `.bin` file. `gpmfinfo.py` reads
each track and prints one JSON summary, used to confirm the sensors that were
recorded and to spot-check their values.

## Output

| field | meaning |
|---|---|
| `devices` | one entry per device ID, in the order the track first mentions it |

Each device has `id` (DVID as a decimal number), `name` (DVNM) and `streams`,
keyed by the four-character key of the sensor data (`ACCL`, `GYRO`, `GPS5`,
…). Each stream has `name` (STNM), `units` (SIUN, or UNIT when there is no
SIUN), `elements` (values per sample), `samples` (samples over the whole
track), `total_samples` (the last TSMP, `null` without one) and `first`: the
values of the first sample after dividing by SCAL (one divisor for all
elements, or one per element), as strings (fractions for integer and
fixed-point types, `%.6g` for floating point, text for character types)),
`last` (the last sample over the whole track, scaled by the SCAL in force for
its payload, in the same form), `orientation` (ORIN, `null` without one) and
`empty` (the EMPT counts of the stream's payloads added up).

GPMF is big-endian. Every entry is padded to a multiple of four bytes.
Complex sample types (`?`) are described by the stream's TYPE entry.

## Layout

- `gpmfkit/gpmf.py` – KLV entries, devices, streams, values
- `gpmfinfo.py` – command line

## Running

    python3 gpmfinfo.py examples/GX010042.bin examples/GX010043.bin
    python3 -m unittest discover -s tests
