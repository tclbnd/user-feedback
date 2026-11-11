"""Procedural textures (standard library only).

Textures are described by plain tuples so they stay picklable for
multiprocessing workers:

    ("checker", scale_u, scale_v, color_a, color_b)

``color_a`` / ``color_b`` are ``(r, g, b)`` triples with 0-255 channels.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

RGB = Tuple[int, int, int]
TextureSpec = Tuple[str, float, float, RGB, RGB]

DEFAULT_CHECKER: TextureSpec = (
    "checker",
    8.0,
    8.0,
    (238, 140, 68),
    (38, 92, 148),
)


def sample(spec: Sequence, u: float, v: float) -> RGB:
    """Evaluate a texture spec at ``(u, v)``; UVs wrap via ``floor``."""
    kind = spec[0]
    if kind == "checker":
        _, scale_u, scale_v, color_a, color_b = spec
        cell = math.floor(u * scale_u) + math.floor(v * scale_v)
        return color_a if cell % 2 == 0 else color_b  # type: ignore[return-value]
    raise ValueError("unknown texture kind %r" % (kind,))
