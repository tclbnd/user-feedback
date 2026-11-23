# mlpkit

Audio check for the lossless tracks of the disc library: Dolby TrueHD streams
demuxed from Blu-ray (`.thd`, sometimes with the AC-3 core frames still
interleaved) and MLP streams from DVD-Audio (`.mlp`). `mlpinfo.py` walks the
access units of each stream and prints one JSON summary.

## Output

| field | meaning |
|---|---|
| `format` | `TrueHD` or `MLP` |
| `sample_rate`, `rate2` | sampling rate of the stream (and of MLP's second channel group, `null` when not used) |
| `bits` | quantisation word length of the first and second channel groups (TrueHD: `[24, 0]`) |
| `channels` | channels of the full presentation (TrueHD: the 8-channel presentation when present, else the 6-channel one) |
| `channel_labels` | TrueHD: the channels of the full presentation in the order of the Dolby TrueHD channel-assignment bits, a pair as its left then right channel, using the labels `L`, `R`, `C`, `LFE`, `Ls`, `Rs`, `Lvh`, `Rvh`, `Lc`, `Rc`, `Lrs`, `Rrs`, `Cs`, `Ts`, `Lsd`, `Rsd`, `Lw`, `Rw`, `Cvh`, `LFE2`; MLP: `null` |
| `channels_6ch` | TrueHD: channels of the 6-channel presentation; MLP: `null` |
| `vbr`, `peak_bitrate`, `substreams` | from the first major sync; peak data rate in bits per second |
| `access_units`, `major_syncs` | access units in the stream and those carrying a major sync |
| `ac3_frames` | AC-3 core frames found between the access units |
| `ac3_bytes`, `ac3_kbps` | bytes of those AC-3 frames, and the bit rate (kbit/s) of the first one from its frame size code; `null` without AC-3 |
| `format_changes` | later major syncs whose stream description differs from the first one |
| `largest_unit` | length in bytes of the longest access unit |
| `skipped_bytes` | bytes before the first major sync |
| `samples`, `duration` | samples per channel and seconds (fraction string) |

The stream description comes from the first major sync; anything before it
cannot be decoded and is skipped.

## Layout

- `mlpkit/mlp.py` – access units, major sync, AC-3 core frames
- `mlpinfo.py` – command line

## Running

    python3 mlpinfo.py examples/feature.thd examples/album.mlp
    python3 -m unittest discover -s tests
