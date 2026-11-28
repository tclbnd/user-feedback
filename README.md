# hcomkit

Sample check for the HCOM-compressed sounds found in old Macintosh games
(sound effects and music stored with the classic Mac HCOM compressor).
`hcominfo.py` decodes each sound the way the Mac sound tools do and prints
one JSON summary per file.

## File layout

| bytes | content |
|---|---|
| 8 | `HCOMPACK` |
| 2 | sample rate (`u16` little-endian) |
| 2 + n | length (`u16` little-endian) and bytes of the HCOM dictionary header as stored in the sound: entry count (`u16` big-endian), delta flag (`u32` big-endian), one entry per node (two `s16` big-endian values), and the first sample value as the last byte |
| 2 | chunk count (`u16` little-endian) |
| | for each chunk its size (`u32` little-endian) and the compressed bytes |

The dictionary is a binary tree: each node holds a left and a right child index
(bit 0 takes the left branch, bit 1 the right one) and a node whose left value
is `-1` is a leaf whose right value is the coded value. The header's last byte
is the first sample of the sound; it is emitted before the coded samples and,
for delta-coded sounds, is also the value the first delta is added to.

The compressed data is one Huffman bit stream split into chunks, read
most-significant bit first. Each chunk restarts at the root of the tree, while
the running sample carries over from one chunk to the next. Samples are 8-bit
unsigned (128 is silence). With delta coding a leaf value is a signed change
added to the previous sample; without it the value is an offset from silence,
so the sample is `value + 128`.

## Output

| field | meaning |
|---|---|
| `rate` | header value |
| `delta` | whether the sound uses delta coding |
| `entries` | dictionary entries |
| `chunks` | samples decoded from each chunk |
| `samples` | total samples, including the first sample from the header |
| `peak`, `sum` | largest absolute value and sum of the samples taken as signed (sample − 128) |
| `crc32` | CRC-32 of the 8-bit samples, lower-case hex |

A file that cannot be decoded gets an `error` entry instead.

## Layout

- `hcomkit/hcom.py` – dictionary and bit stream decoder
- `hcominfo.py` – command line

## Running

    python3 hcominfo.py examples/door.hcom examples/theme.hcom
    python3 -m unittest discover -s tests
