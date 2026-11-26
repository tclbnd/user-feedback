# gbapack

Check for the compressed graphics the art team extracts from GBA and DS game
ROMs (`.lz`, one compressed block per file, as passed to the BIOS
decompression calls). `packinfo.py` decompresses each block and prints a JSON
summary, so redrawn 4bpp tile sets can be compared with the originals.

## Input

A block starts with the 4-byte BIOS header: the compression type in the
first byte and the decompressed size (24-bit little-endian) in the other
three. Supported: LZ77 (`0x10`), the DS LZ variant (`0x11`), 8-bit Huffman
(`0x28`), run-length (`0x30`) and the 8-bit and 16-bit difference filters
(`0x81`, `0x82`).

## Output

| field | meaning |
|---|---|
| `type` | `"lz77"`, `"lz11"`, `"huffman8"`, `"rle"`, `"diff8"` or `"diff16"` |
| `size` | decompressed size from the header |
| `complete` | `true` when the block holds enough data for the whole output |
| `crc32` | CRC-32 of the decompressed data, 8 lower-case hex digits |
| `tiles`, `blank_tiles` | 32-byte 4bpp tiles in the output, and how many are all zero |

`crc32` and the tile counts are `null` when the block is incomplete.

## Layout

- `gbapack/unpack.py` – header and decompressors
- `packinfo.py` – command line

## Running

    python3 packinfo.py examples/font.lz examples/stage.lz
    python3 -m unittest discover -s tests
