"""Deterministic buffer digests shared by the acceptance and golden tools."""

from __future__ import annotations

import hashlib
from typing import Dict, Optional

from rasterizer import RenderResult


def _sha(parts) -> str:
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _float_token(value: Optional[float]) -> bytes:
    return b"N" if value is None else value.hex().encode("ascii")


def buffer_digests(result: RenderResult) -> Dict[str, str]:
    """Return SHA-256 digests for pixels, depth, coverage, UV and primitive ids."""
    pixel_parts = []
    depth_parts = []
    coverage_parts = []
    uv_parts = []
    pid_parts = []

    for y in range(result.height):
        for x in range(result.width):
            pixel = result.pixels[y][x]
            pixel_parts.append(bytes((pixel[0], pixel[1], pixel[2])))

            depth = result.depth_values[y][x]
            depth_parts.append(_float_token(depth))

            coverage_parts.append(bytes((result.coverage[y][x],)))

            uv = None if result.uv_values is None else result.uv_values[y][x]
            if uv is None:
                uv_parts.append(b"N")
            else:
                uv_parts.append(
                    uv[0].hex().encode("ascii") + b"," +
                    uv[1].hex().encode("ascii")
                )

            pid = result.primitive_ids[y][x]
            pid_parts.append(b"N" if pid is None else str(pid).encode("ascii"))

    return {
        "pixels": _sha(pixel_parts),
        "depth": _sha(depth_parts),
        "coverage": _sha(coverage_parts),
        "uv": _sha(uv_parts),
        "primitive_ids": _sha(pid_parts),
    }


def image_digest(rows) -> str:
    parts = []
    for row in rows:
        for pixel in row:
            parts.append(bytes((pixel[0], pixel[1], pixel[2])))
    return _sha(parts)
