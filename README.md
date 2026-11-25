# audkit

Catalogue check for the Westwood Studios game audio (`.aud` speech, music and
effects) in the retro game archive. `audinfo.py` prints one JSON summary per
file, used to check the format, the chunk structure and the decoded level.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `channels`, `bits` | from the header (integers) |
| `codec` | `westwood_adpcm` or `ima_adpcm` |
| `chunks`, `bad_chunk_ids` | the number of chunks, and of chunks whose id is not the Westwood chunk id |
| `data_bytes` | compressed bytes in the chunks (an integer) |
| `header_size_ok` | `true` when the header's compressed size equals the chunks with their headers |
| `output_bytes` | the decompressed size the header gives |
| `chunk_output_bytes` | the decompressed sizes the chunk headers give, added up |
| `frames`, `duration` | `output_bytes` in sample frames, and seconds as a fraction string |
| `truncated` | `true` when the file ends before the chunks the header announces |
| `peak`, `last_sample` | for IMA ADPCM: the largest decoded magnitude as a fraction of 32768 (`%.6f`) and the last decoded sample (an integer), decoding all chunk data as one stream; else `null` |

Only the bytes the header's compressed size covers belong to the audio.
The header, flags, chunk headers and the IMA ADPCM decoding (as Westwood's
games do it) follow the Westwood AUD format and the IMA ADPCM algorithm.

## Format

All integers are little-endian. The 12-byte file header is

| offset | type | field |
|---|---|---|
| 0 | u16 | sample rate |
| 2 | u32 | compressed size (audio bytes following the file header) |
| 6 | u32 | uncompressed size (decoded PCM bytes) |
| 10 | u8 | flags: bit 0 = 16-bit samples, bit 1 = stereo, bit 2 = signed |
| 11 | u8 | codec: `1` Westwood SND1 ADPCM, `99` IMA ADPCM |

Each 8-byte chunk header is

| offset | type | field |
|---|---|---|
| 0 | u16 | compressed payload size |
| 2 | u16 | uncompressed (decoded) size |
| 4 | u16 | chunk id, `0xDEAF` |
| 6 | u16 | chunk number |

A chunk whose declared payload runs past the header's compressed size (or the
end of the file) is the incomplete last chunk and sets `truncated`; only the
bytes actually covered are decoded. For codec `99`, each payload byte holds two
nibbles (low nibble first); stereo streams alternate channels, and the IMA
predictor and step-index state carry over from one chunk to the next.

## Layout

- `audkit/aud.py` – header, chunks, decoding
- `audinfo.py` – command line

## Running

    python3 audinfo.py examples/eva_ready.aud examples/hell_march.aud
    python3 -m unittest discover -s tests
