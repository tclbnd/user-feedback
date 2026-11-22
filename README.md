# cafkit

Catalogue checker for the voice memos and field recordings exported from the
iOS recorder app as Core Audio Format (`.caf`) files. `cafinfo.py` prints one
JSON summary per file.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `format`, `channels`, `bits` | from the `desc` chunk (`format` without trailing spaces) |
| `bytes_per_packet`, `frames_per_packet` | from `desc` (0 means the value varies per packet) |
| `float`, `little_endian` | for `lpcm` only, from the format flags (`null` for other formats) |
| `chunks` | chunk types in file order |
| `edit_count` | edit count at the start of the `data` chunk |
| `data_bytes` | bytes of audio data (the edit count is not audio) |
| `packets` | packets of audio, from the packet table or `data_bytes` / `bytes_per_packet` |
| `frames` | playable frames: priming and remainder frames are not counted |
| `priming`, `remainder` | from the packet table (0 without one) |
| `duration` | `frames` / `sample_rate` in seconds, as a fraction string |
| `max_packet_bytes`, `max_packet_frames` | largest single packet in bytes and in frames, for buffer sizing |
| `info` | `info` chunk strings as a key/value object |
| `channel_layout` | `tag` and `bitmap` from the `chan` chunk, or `null` |
| `aac` | for AAC files, the decoder configuration from the magic cookie (`kuki`): `object_type` of the core codec, `sbr` and `ps` for HE-AAC and HE-AAC v2, `sample_rate` of the core, `output_sample_rate` after SBR, `channel_config`; `null` otherwise |

## Format notes

The parser follows Apple's `CAFFile.h` and the Core Audio Format spec, so files
from tools other than the iOS recorder also read correctly:

- A chunk size of `-1` (or any negative value) means the chunk runs to the end
  of the file. This is the normal shape of a recording interrupted by a power
  loss: the `data` chunk was never given its final size, so its length comes
  from the file length rather than the header.
- A `data` size larger than the bytes present is a write that stopped early and
  is clipped to what is on disk.
- The first four bytes of `data` are the edit count and are never counted in
  `data_bytes`.
- Packet table entries are big-endian base-128 variable length integers. A
  variable `bytes_per_packet` gives one entry per packet (bytes); a variable
  `frames_per_packet` gives one entry (frames); when both vary each packet has
  a bytes entry followed by a frames entry. `max_packet_frames` comes from those
  entries, not from the fixed `frames_per_packet`.
- `frames` is the packet table's `mNumberValidFrames` (already exclusive of
  priming and remainder) when it is set, otherwise the total frames implied by
  the packets minus priming and remainder.
- The `info` chunk entry count is big endian, like every other CAF integer.
- The AAC magic cookie may be a raw `AudioSpecificConfig` or an ESDS descriptor
  chain (with or without a short prefix). Explicit HE-AAC / HE-AAC v2 object
  types `5` / `29` and backwards compatible SBR/PS signalling are both handled.

## Layout

- `cafkit/caf.py` – chunks, audio description, packet table
- `cafinfo.py` – command line

## Running

    python3 cafinfo.py examples/voice_memo.caf examples/field.caf
    python3 -m unittest discover -s tests
