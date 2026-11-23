# lufskit

Loudness check for the programme deliveries (stereo and 5.1 WAV masters)
before they go to broadcast. `lufsinfo.py` measures each file per ITU-R
BS.1770-4 and EBU R 128 and prints one JSON summary; deliveries must sit at
-23 LUFS integrated.

The measurement applies the two BS.1770-4 K-weighting stages to every channel,
sums the weighted mean square over the channels (L/R/C weight 1, surround
weight 1.41, LFE excluded) and reads `-0.691 + 10 log10` of that power.
Integrated loudness uses 400 ms blocks with a -70 LUFS absolute gate followed
by a -10 LU relative gate; loudness range uses 3 s blocks with a -70 LUFS
absolute gate followed by a -20 LU relative gate (EBU Tech 3342).

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
| `gated_blocks` | momentary blocks that pass both gates of the integrated measurement |
| `loudness_range` | loudness range, LU (EBU Tech 3342): the short-term blocks above −70 LUFS and above the relative gate 20 LU below their mean power, sorted by loudness; the value at index `round((n − 1) × 0.95)` minus the value at index `round((n − 1) × 0.10)` |

Loudness values are `%.1f`, or `null` when there is nothing to measure.
Blocks start every 100 ms. Channel roles come from the WAVE_FORMAT_EXTENSIBLE
channel mask when the file has one, otherwise from the usual order for the
channel count (mono C; stereo L R; 5.1 L R C LFE Ls Rs). Surround channels are
weighted 1.41 and the LFE channel is left out of the measurement. Samples are
8-bit, 16-bit, 24-bit or 32-bit PCM, or 32/64-bit float; the K-weighting
coefficients are derived for the file's own sample rate, so any rate from
8 kHz to 192 kHz measures the same programme level.

## Layout

- `lufskit/lufs.py` – WAV reading, K-weighting, blocks, gating
- `lufsinfo.py` – command line

## Running

    python3 lufsinfo.py examples/promo_stereo.wav examples/trailer_51.wav
    python3 -m unittest discover -s tests
