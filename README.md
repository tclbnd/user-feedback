# lufskit

Loudness check for the programme deliveries (stereo and 5.1 WAV masters)
before they go to broadcast. `lufsinfo.py` measures each file per ITU-R
BS.1770-4 and EBU R 128 and prints one JSON summary; deliveries must sit at
-23 LUFS integrated.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `channels` | from the WAV header |
| `measured_channels` | channels that take part in the measurement (the LFE channel does not) |
| `duration` | seconds, `%.3f` |
| `sample_peak` | largest sample magnitude in dBFS, `%.1f` (`null` for digital silence) |
| `momentary_max` | highest momentary loudness (400 ms blocks), LUFS |
| `short_term_max` | highest short-term loudness (3 s blocks), LUFS |
| `integrated` | gated programme loudness, LUFS |

Loudness values are `%.1f`, or `null` when there is nothing to measure.
Blocks start every 100 ms. Channel roles come from the WAVE_FORMAT_EXTENSIBLE
channel mask when the file has one, otherwise from the usual order for the
channel count (mono C; stereo L R; 5.1 L R C LFE Ls Rs). Surround channels are
weighted 1.41. Samples are 16-bit or 24-bit PCM, or 32-bit float.

## Layout

- `lufskit/lufs.py` – WAV reading, K-weighting, blocks, gating
- `lufsinfo.py` – command line

## Running

    python3 lufsinfo.py examples/promo_stereo.wav examples/trailer_51.wav
    python3 -m unittest discover -s tests
