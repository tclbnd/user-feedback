# flackit

Checker for the lossless archive (FLAC masters and rips, some with an ID3v2
tag in front). `flacinfo.py` prints one JSON summary per file without decoding
audio: metadata blocks plus a walk over the frame headers.

## Output

| field | meaning |
|---|---|
| `id3v2_bytes` | bytes of an ID3v2 tag before `fLaC` (0 if none) |
| `min_block` … `total_samples` | STREAMINFO fields (`channels` and `bits` are the real values, not minus one) |
| `metadata` | metadata blocks in order: `type`, `length` |
| `seek_points`, `placeholders` | SEEKTABLE points that refer to a sample, and placeholder points |
| `tags` | Vorbis comment fields as stored |
| `frames` | frames whose header and frame checksum are both valid |
| `frame_samples` | sum of their block sizes |
| `blocking` | `fixed` or `variable` (from the first frame) |
| `first_number`, `last_number` | coded number of the first and last frame (frame number or sample number) |
| `rates`, `frame_bits` | distinct sample rates / sample sizes found in frame headers |
| `channel_modes` | frames per channel assignment: `independent`, `left_side`, `right_side`, `mid_side` |
| `bad_frames` | frames with a valid header whose frame checksum does not match |

## Layout

- `flackit/flac.py` – metadata blocks, frame headers, frame walk
- `flacinfo.py` – command line

## Running

    python3 flacinfo.py examples/master.flac examples/rip.flac
    python3 -m unittest discover -s tests
