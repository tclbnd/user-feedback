# sbcscan

Quality check for Bluetooth audio captured from headsets and car kits.
`sbcinfo.py` reads a capture and prints one JSON summary per file.

## Inputs

- `.sbc` – raw SBC frames as sent over A2DP
- `.msbc` – wide-band speech from HFP: mSBC frames, either bare or each wrapped
  in the 2-byte H2 synchronisation header (60 bytes per packet with padding)
- `.a2dp` – A2DP media payloads exported from a packet capture, each payload
  preceded by its length as a 2-byte big-endian integer; a payload is the
  1-byte media payload header followed by SBC frame data

## Output

| field | meaning |
|---|---|
| `codec` | `sbc` or `msbc` |
| `sample_rate`, `channel_mode`, `blocks`, `subbands`, `allocation`, `bitpool` | from the first valid frame |
| `frame_length` | bytes of the first valid frame |
| `bitrate` | bit/s of the first valid frame (integer part) |
| `frames` | number of valid frames |
| `duration` | seconds as a fraction, from the PCM samples the valid frames carry |
| `invalid_frames` | frames with a header that cannot be valid (skipped) |
| `lost_frames` | mSBC packets missing according to the H2 sequence numbers |

## Layout

- `sbckit/header.py` – SBC frame header
- `sbckit/stream.py` – stream walk and summary
- `sbcinfo.py` – command line

## Running

    python3 sbcinfo.py examples/headset_music.sbc examples/carkit_call.msbc examples/phone_stream.a2dp
    python3 -m unittest discover -s tests
