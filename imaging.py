"""Dependency-free image encoding and spectral ribbon visualisation.

Only 8-bit true-colour PNG is needed for the validation artefacts (spectral
integration image, difference map).  Encoding uses zlib deflate on filtered
scanlines; no third-party imaging library is required.
"""
from __future__ import annotations

import struct
import zlib
from typing import List, Sequence, Tuple

RGB = Tuple[int, int, int]


def write_png(path: str, width: int, height: int,
              rows: Sequence[Sequence[RGB]]) -> None:
    """Write an 8-bit RGB PNG given row-major pixel tuples."""
    if len(rows) != height or any(len(r) != width for r in rows):
        raise ValueError("pixel buffer does not match width/height")

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0 (none) per scanline
        for (r, g, b) in row:
            raw.extend((r & 255, g & 255, b & 255))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def to_byte(v: float) -> int:
    if v <= 0.0:
        return 0
    if v >= 1.0:
        return 255
    return int(round(v * 255.0))


def display_row(linear_rgb_rows: Sequence[Sequence[Sequence[float]]],
                exposure: float = 1.0) -> List[List[RGB]]:
    """Convert linear sRGB pixel rows to 8-bit encoded rows."""
    from spectra import linear_srgb_to_display
    out = []
    for row in linear_rgb_rows:
        new_row = []
        for px in row:
            enc = linear_srgb_to_display(px, exposure=exposure)
            new_row.append((to_byte(enc[0]), to_byte(enc[1]),
                            to_byte(enc[2])))
        out.append(new_row)
    return out


# Approximate display colour per wavelength band for ribbon visualisation.
# Hue ramp blue -> cyan -> green -> yellow -> red, intentionally simple.
def wavelength_rgb(wavelength_nm: float) -> RGB:
    w = float(wavelength_nm)
    if w < 450.0:
        t = max(0.0, min(1.0, (w - 380.0) / 70.0))
        return (int(80 * t), 0, 180 + int(60 * (1.0 - t)))
    if w < 500.0:
        t = (w - 450.0) / 50.0
        return (0, int(220 * t), 240 - int(60 * t))
    if w < 570.0:
        t = (w - 500.0) / 70.0
        return (int(220 * t), 220, 20)
    if w < 630.0:
        t = (w - 570.0) / 60.0
        return (230, 230 - int(180 * t), 10)
    t = max(0.0, min(1.0, (w - 630.0) / 120.0))
    return (235, int(50 * (1.0 - t)), 10)


def ribbon_image(width: int, height: int,
                 band_centers_y: Sequence[float],
                 band_width: float,
                 intensities: Sequence[float],
                 wavelengths_nm: Sequence[float],
                 background: RGB = (12, 12, 16)) -> List[List[RGB]]:
    """Paint Gaussian spectral bands onto a 2D buffer.

    ``band_centers_y`` and ``intensities`` are per wavelength; coordinates
    are normalised to [0, 1] vertically.  Bands are additive then clipped,
    mimicking the spectral integration the renderer performs.
    """
    rows = [[background for _ in range(width)] for _ in range(height)]
    for y in range(height):
        ny = 1.0 - (y + 0.5) / height  # image row 0 is the top
        for x in range(width):
            acc = [float(background[c]) for c in range(3)]
            for wi, cy in enumerate(band_centers_y):
                g = __import__("math").exp(
                    -0.5 * ((ny - cy) / band_width) ** 2)
                r, gcol, b = wavelength_rgb(wavelengths_nm[wi])
                k = g * float(intensities[wi])
                acc[0] += k * r
                acc[1] += k * gcol
                acc[2] += k * b
            rows[y][x] = tuple(max(0, min(255, int(round(v))))
                               for v in acc)
    return rows


def difference_image(width: int, height: int,
                     values_a: Sequence[float],
                     values_b: Sequence[float],
                     scale: float = 10.0) -> List[List[RGB]]:
    """Signed scalar difference map: negative blue, positive red."""
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            # Both inputs are column signals expanded across the width.
            idx = min(len(values_a) - 1,
                      int((x + 0.5) / width * len(values_a)))
            d = (values_a[idx] - values_b[idx]) * scale
            if d >= 0.0:
                row.append((min(255, int(round(d * 255))), 20, 20))
            else:
                row.append((20, 20, min(255, int(round(-d * 255)))))
        rows.append(row)
    return rows
