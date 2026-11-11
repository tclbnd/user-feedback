"""Minimal 8-bit RGB PNG encoder built on ``zlib`` and ``struct``.

The encoder writes a truecolour (colour type 2) PNG with filter type 0 on
every scanline.  No third-party image library is used.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable, Sequence, Tuple

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

RGB = Tuple[int, int, int]


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", crc)


def _clip_byte(value) -> int:
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        value = 0
    return 0 if value < 0 else 255 if value > 255 else value


def encode_png(width: int, height: int, rows: Iterable[Sequence[Sequence[int]]]) -> bytes:
    """Return the PNG bytes for an ``height`` x ``width`` RGB image.

    *rows* must contain ``height`` sequences of ``width`` ``(r, g, b)``
    triples.  Values outside 0..255 are clamped.
    """
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")

    raw = bytearray()
    row_count = 0
    for row in rows:
        if len(row) != width:
            raise ValueError(
                "row %d has %d pixels, expected %d" % (row_count, len(row), width)
            )
        raw.append(0)  # filter type: None
        for pixel in row:
            if len(pixel) < 3:
                raise ValueError("each pixel needs at least three channels")
            raw.append(_clip_byte(pixel[0]))
            raw.append(_clip_byte(pixel[1]))
            raw.append(_clip_byte(pixel[2]))
        row_count += 1

    if row_count != height:
        raise ValueError(
            "received %d rows, expected %d" % (row_count, height)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def write_png(path: str, width: int, height: int, rows) -> None:
    """Encode an RGB image and write it to *path*."""
    data = encode_png(width, height, rows)
    with open(path, "wb") as handle:
        handle.write(data)
