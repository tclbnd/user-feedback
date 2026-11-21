# scte

Decoder for ad-insertion cue messages logged by the playout monitor (SCTE 35
`splice_info_section`, one message per line, base64 as found in HLS/DASH
manifests or `0x`-prefixed hex as printed by the TS analyser). `scteinfo.py`
prints one JSON object per message.

## Output

| field | meaning |
|---|---|
| `protocol_version`, `sap_type`, `tier`, `encrypted` | section header fields |
| `pts_adjustment` | seconds (fraction string) |
| `command` | `splice_null`, `splice_insert`, `time_signal`, `private_command`, … |
| `crc_ok` | whether the section CRC matches |
| `splice_insert` | `event_id`, `cancel`; unless cancelled: `out_of_network`, `immediate`, `time` (program splice) or `components` (`tag`, `time`), `break` (`auto_return`, `duration`) when present, `unique_program_id`, `avail_num`, `avails_expected` |
| `time` | splice time of `time_signal` |
| `descriptors` | `avail` (`provider_avail_id`), `segmentation` (see below), others as `tag`, `identifier`, `bytes` (descriptor length) |

All times are in seconds as fraction strings; splice times include
`pts_adjustment` and are `null` when no time is specified. Encrypted messages
report only `protocol_version`, `sap_type`, `tier`, `pts_adjustment`, `encrypted`,
`command` and `crc_ok`.

Segmentation descriptors: `event_id`, `cancel`; unless cancelled:
`restrictions` (only when delivery is restricted), `components` (component
mode: `tag`, `pts_offset`), `duration`, `upid_type`, `upid` (text for text
types, `{format, private}` for MPU, a list of `{type, value}` for MID, hex
otherwise), `type_id`, `segment_num`, `segments_expected`, and
`sub_segment_num` / `sub_segments_expected` when the segmentation type carries them.

## Layout

- `scte/bits.py` – bit reader
- `scte/splice.py` – section, commands, descriptors
- `scteinfo.py` – command line

## Running

    python3 scteinfo.py examples/playout.log examples/analyser.txt
    python3 -m unittest discover -s tests
