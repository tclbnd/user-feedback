# audkit

Catalogue check for the Westwood Studios game audio (`.aud` speech, music and
effects) in the retro game archive. `audinfo.py` prints one JSON summary per
file, used to check the format, the chunk structure and the decoded level.

## Output

| field | meaning |
|---|---|
| `sample_rate`, `channels`, `bits` | from the header (integers); flags bit 0 is stereo and bit 1 is 16-bit |
| `codec` | `westwood_adpcm` (1) or `ima_adpcm` (99) |
| `chunks`, `bad_chunk_ids` | the number of chunks, and of chunks whose id is not `0x0000DEAF` |
| `data_bytes` | compressed payload bytes in the chunks (an integer), excluding chunk headers |
| `header_size_ok` | `true` when payload bytes plus the 8-byte chunk headers equal the header's compressed size |
| `output_bytes` | the decompressed size the header gives |
| `chunk_output_bytes` | the decompressed sizes the chunk headers give, added up |
| `frames`, `duration` | `output_bytes` in sample frames, and seconds as a fraction string |
| `truncated` | `true` when the file ends before the bytes the header's compressed size announces |
| `peak`, `last_sample` | for IMA ADPCM: the largest decoded magnitude as a fraction of 32768 (`%.6f`) and the last decoded sample (an integer), decoding all chunk data as one stream; else `null` |

Only the bytes the header's compressed size covers belong to the audio.
The file header stores its compressed size first and its decompressed size
second; the chunk headers (`<HHI`, id `0x0000DEAF`) are little-endian.
The IMA ADPCM state is not reset between chunks, so the whole file is decoded
as one continuous nibble stream (low nibble of each byte first).

## Layout

- `audkit/aud.py` – header, chunks, decoding
- `audinfo.py` – command line

## Running

    python3 audinfo.py examples/eva_ready.aud examples/hell_march.aud
    python3 -m unittest discover -s tests
