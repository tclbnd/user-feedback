"""Small PNG decoder used by the acceptance tests.

It understands the subset produced by :mod:`png_writer`: 8-bit truecolour
(colour type 2), no interlace, and filter type 0 on every scanline.  Chunk
CRCs are verified so a corrupt golden file is detected.
"""

from __future__ import annotations

import struct
import zlib
from typing import List, Tuple

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

Pixel = Tuple[int, int, int]


def decode_png(path: str):
    with open(path, "rb") as handle:
        data = handle.read()
    return decode_png_bytes(data)


def decode_png_bytes(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("missing PNG signature")

    position = len(PNG_SIGNATURE)
    width = height = bit_depth = colour_type = 0
    idat = bytearray()
    saw_ihdr = saw_iend = False

    while position < len(data):
        if position + 8 > len(data):
            raise ValueError("truncated chunk header")
        length, chunk_type = struct.unpack(">I4s", data[position:position + 8])
        position += 8
        chunk_data = data[position:position + length]
        if len(chunk_data) != length:
            raise ValueError("truncated %r chunk" % chunk_type)
        position += length
        if position + 4 > len(data):
            raise ValueError("truncated chunk CRC")
        crc = struct.unpack(">I", data[position:position + 4])[0]
        position += 4
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != crc:
            raise ValueError("bad CRC for %r chunk" % chunk_type)

        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("bad IHDR length")
            (width, height, bit_depth, colour_type,
             compression, filtering, interlace) = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if (bit_depth, colour_type, compression, filtering, interlace) != (
                8, 2, 0, 0, 0
            ):
                raise ValueError("unsupported PNG format")
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        elif chunk_type in (b"PLTE", b"tRNS", b"gAMA", b"sRGB", b"pHYs"):
            continue
        else:
            # Unknown ancillary chunks are allowed by the PNG specification.
            if chunk_type[:1].isupper():
                raise ValueError("unknown critical chunk %r" % chunk_type)

    if not saw_ihdr:
        raise ValueError("missing IHDR")
    if not saw_iend:
        raise ValueError("missing IEND")
    if width <= 0 or height <= 0:
        raise ValueError("invalid image dimensions")

    raw = zlib.decompress(bytes(idat))
    stride = 1 + 3 * width
    expected = stride * height
    if len(raw) != expected:
        raise ValueError(
            "decompressed size is %d, expected %d" % (len(raw), expected)
        )

    rows: List[List[Pixel]] = []
    for row in range(height):
        start = row * stride
        if raw[start] != 0:
            raise ValueError("unsupported filter type %d" % raw[start])
        pixels: List[Pixel] = []
        offset = start + 1
        for _ in range(width):
            pixels.append((raw[offset], raw[offset + 1], raw[offset + 2]))
            offset += 3
        rows.append(pixels)
    return width, height, rows
