# sphkit

Check for the speech corpora the ASR team licenses (NIST SPHERE `.sph` /
`.wav` files from TIMIT, Switchboard and similar collections). `sphinfo.py`
prints one JSON summary per file, used to check the header fields, sample
format and data integrity before the audio goes into training.

## Output

| field | meaning |
|---|---|
| `header_bytes` | the header size the second header line gives (an integer) |
| `fields` | every header field by name: `-i` values as integers, `-r` values as strings, `-sN` values as exactly N characters |
| `channels`, `sample_rate`, `sample_count`, `bytes_per_sample` | from the header (`channel_count` defaults to 1, `sample_n_bytes` to 2; `sample_count` is per channel and, when missing, computed from the data for uncompressed files) |
| `coding` | the first item of `sample_coding` (`pcm`, `ulaw`, `alaw`; `pcm` when missing) |
| `compressed` | `true` when `sample_coding` names an embedded compression (e.g. shorten) |
| `byte_order` | `little`, `big` or `single` from `sample_byte_format` (`01`/`0123`, `10`/`3210`, `1`) |
| `duration` | `sample_count / sample_rate` in seconds as a fraction string |
| `data_bytes` | bytes after the header |
| `complete` | `true` when the data holds `sample_count × channels` samples (`null` for compressed files) |
| `checksum_ok` | for 16-bit PCM with a `sample_checksum` field: whether the sum of the samples as unsigned 16-bit values modulo 65536 matches; else `null` |
| `peak` | the largest sample magnitude as a fraction of full scale, `%.6f` (µ-law and A-law decoded to 16-bit linear; 8-bit PCM is signed); `null` for compressed files |

The header layout and the field types follow the NIST SPHERE format.

## Layout

- `sphkit/sph.py` – header, sample format, checks
- `sphinfo.py` – command line

## Running

    python3 sphinfo.py examples/sa1.sph examples/sw02001.sph
    python3 -m unittest discover -s tests
