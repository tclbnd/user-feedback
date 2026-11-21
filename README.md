# latmkit

Inspector for AAC audio carried in LOAS/LATM (DVB and ATSC broadcast captures,
`.latm` / `.loas` dumps from the ingest recorder). `latminfo.py` prints one JSON
summary per file.

## Output

| field | meaning |
|---|---|
| `mux_version` | `audioMuxVersion` of the StreamMuxConfig |
| `object_type` | AAC audio object type of the core coder (2 for AAC LC, also when SBR/PS is signalled) |
| `sample_rate` | core sampling frequency in Hz |
| `ext_sample_rate` | SBR output sampling frequency in Hz, `null` without SBR signalling |
| `ps` | whether Parametric Stereo is signalled |
| `channels` | output channel count from the channel configuration |
| `frame_length` | samples per access unit at the core rate (1024 or 960) |
| `loas_frames` | AudioSyncStream frames, counted from the first frame that carries a StreamMuxConfig |
| `access_units` | AAC access units (payloads) in those frames |
| `configs` | frames that carry a new StreamMuxConfig |
| `payload_bytes` | total bytes of all access-unit payloads |
| `duration` | `access_units * frame_length / sample_rate` seconds as a fraction string |

Only one program and one layer are supported; frameLengthType must be 0.

Parsing follows ISO/IEC 14496-3: the sync layer resynchronises on the 11-bit
AudioSyncStream syncword, so captures that start with stray bytes are handled.
Both `audioMuxVersion` 0 and 1 StreamMuxConfigs are read, including the
`ascLen` wrapper and its padding, `otherData` and `crcCheckPresent` fields, and
payload lengths accumulate over repeated `0xFF` bytes. HE-AAC is reported with
the core coder's object type and sampling rate; the SBR/PS rates come from
either the hierarchical (audioObjectType 5/29) or the backward-compatible
syncExtensionType signalling.

## Layout

- `latmkit/bits.py` – bit reader
- `latmkit/latm.py` – LOAS sync layer, StreamMuxConfig, AudioSpecificConfig, payloads
- `latminfo.py` – command line

## Running

    python3 latminfo.py examples/studio.latm examples/broadcast.loas
    python3 -m unittest discover -s tests
