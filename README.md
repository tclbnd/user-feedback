# dpcmkit

Check for the DPCM sound the team pulls out of old PC game files: Interplay
MVE movies, Origin's Wing Commander III/IV (Xan) and Sierra SOL sound
resources. The extraction tool stores the audio packets of one stream in a
packet file (`.dpk`); `dpcminfo.py` decodes them in order and prints one JSON
summary per file.

## File layout

`DPCMPAK` and a NUL, a four-byte codec tag (`IPLY` Interplay, `XAN ` Xan,
`SOL1` and `SOL2` the two 8-bit Sierra SOL tables, `SOL3` 16-bit SOL), the
channel count (1 or 2), the sample rate (`u32` little-endian), the packet
count (one byte), then for each packet its length (`u32` little-endian) and
the packet exactly as stored in the game file.

The decoder is set up once for the stream and fed the packets in order. A
packet that cannot be decoded counts 0 samples and the next packet goes on.

## Output

| field | meaning |
|---|---|
| `codec`, `channels`, `bits`, `rate` | stream parameters; `bits` is 8 for the 8-bit SOL tables and 16 otherwise |
| `packets` | samples (all channels together) each packet produced |
| `frames`, `duration` | sample frames over all packets and their length in seconds (four decimals) |
| `channel` | one object per channel: `samples`, `peak` and `sum` of the samples (8-bit samples counted around 128), and `crc32` of the channel's samples as stored (8-bit unsigned, or 16-bit signed little-endian), lower-case hex |

## Layout

- `dpcmkit/dpcm.py` – packet decoders
- `dpcminfo.py` – command line

## Running

    python3 dpcminfo.py examples/mve.dpk examples/wc3.dpk examples/king.dpk
    python3 -m unittest discover -s tests
