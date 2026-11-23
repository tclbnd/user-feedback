# burstkit

Check for the S/PDIF captures of the disc audio (16-bit stereo WAV files that
carry compressed audio as IEC 61937 data bursts). `burstinfo.py` finds the
bursts in each capture and prints one JSON summary, used to confirm what each
capture carries and that no bursts went missing.

## Output

| field | meaning |
|---|---|
| `sample_rate` | from the WAV header |
| `byte_order` | `little` (normal) or `big` (16-bit words stored byte-swapped by the capture tool) |
| `frames` | stereo sample frames in the data chunk |
| `streams` | per bitstream number and data type, keyed `"<number>/<type>"` |
| `pauses` | pause bursts |
| `irregular` | per stream key, bursts that do not start one repetition period (in frames) after the stream's previous burst |
| `bsmod` | per stream key, the bit stream mode from Pc bits 8–10 of the stream's first burst for AC-3 and E-AC-3, else `null` |
| `pause_frames` | total gap length the pause bursts announce (the first payload word of each pause burst, in frames) |

Types: `AC-3`, `MPEG-1 L1`, `MPEG-1 L2/L3`, `MPEG-2 ext`, `AAC`,
`MPEG-2 L1 LSF`, `MPEG-2 L2/L3 LSF`, `DTS-I`, `DTS-II`, `DTS-III`, `E-AC-3`,
`TrueHD`, else `type_N`. Null bursts are skipped.

Each stream has `bursts`, `payload_bytes` (the burst payload length from Pd),
`errors` (bursts with the error flag set) and `seconds` (bursts times the
repetition period of the data type, divided by the sample rate, as a fraction
string).

## Layout

- `burstkit/burst.py` – WAV reading, burst search, burst info
- `burstinfo.py` – command line

## Running

    python3 burstinfo.py examples/film_ac3.wav examples/concert_dts.wav
    python3 -m unittest discover -s tests
