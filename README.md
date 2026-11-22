# mpckit

Catalogue checker for the Musepack (SV8) part of the music archive.
`mpcinfo.py` prints one JSON summary per `.mpc` file.

## Output

| field | meaning |
|---|---|
| `id3v2_bytes` | bytes of ID3v2 tags in front of `MPCK` (0 if none) |
| `version`, `sample_rate`, `channels`, `max_band`, `mid_side` | from the stream header (`SH`) |
| `frames_per_packet` | MPC frames (1152 samples each) in one audio packet |
| `samples`, `beginning_silence` | from `SH` |
| `duration` | playable samples (`samples` − `beginning_silence`) ÷ sample rate, as a fraction string |
| `frames` | MPC frames needed for `samples` |
| `header_crc_ok` | the CRC32 in `SH` matches the rest of the packet |
| `audio_packets`, `chapters` | number of `AP` and `CT` packets |
| `replay_gain` | from `RG`: gains in dB as fraction strings, peaks as stored |
| `encoder` | from `EI`: `profile` (fraction string), `pns`, `version` |
| `stream_end` | an `SE` packet was found; bytes after it (tags) are not packets |
| `packets` | packet keys in file order |

## Layout

- `mpckit/mpc.py` – packets, stream header, replay gain, encoder info
- `mpcinfo.py` – command line

## Running

    python3 mpcinfo.py examples/album.mpc examples/talk.mpc
    python3 -m unittest discover -s tests
