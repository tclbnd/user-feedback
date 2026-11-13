"""Spectral bookkeeping: wavelength samples, XYZ basis and display mapping.

Wavelengths are sampled on an explicit grid (nm).  Configs may supply their
own ``xyz_basis``; when absent, a smooth synthetic Gaussian observer basis is
generated so the pipeline stays dependency-free while still producing
separate long/medium/short channel responses.

The display conversion keeps the linear pipeline intact:

    spectral sample weights -> linear XYZ -> linear sRGB -> encoded sRGB
"""
from __future__ import annotations

import math
from typing import List, Sequence

# sRGB D65 primaries expressed in CIE XYZ, column-major-ish rows as the
# standard matrix M (linear sRGB = M @ XYZ relative to D65 white).
_XYZ_TO_SRGB = (
    (3.2406, -1.5372, -0.4986),
    (-0.9689, 1.8758, 0.0415),
    (0.0557, -0.2040, 1.0570),
)
_D65_WHITE = (0.95047, 1.0, 1.08883)


def wavelength_spacing_weights(wavelengths_nm: Sequence[float]) -> List[float]:
    """Trapezoidal integration weights (nm) normalised to mean weight 1.

    Uniform grids therefore give every wavelength weight 1, matching the
    original ``sum(...) / n`` convention; non-uniform grids get proper
    interval widths.
    """
    n = len(wavelengths_nm)
    if n == 1:
        return [1.0]
    raw = [0.0] * n
    for i in range(n - 1):
        d = float(wavelengths_nm[i + 1]) - float(wavelengths_nm[i])
        raw[i] += 0.5 * d
        raw[i + 1] += 0.5 * d
    mean = sum(raw) / n
    return [w / mean for w in raw]


def synthetic_xyz_basis(wavelengths_nm: Sequence[float]):
    """Gaussian pseudo-observer: S (~445 nm), M (~545 nm), L (~600 nm).

    Returns a list of [x, y, z] rows per wavelength.  The Y (green) response
    peaks near the middle of the visible band, as a sanity-check stand-in for
    a real CMF table.
    """
    peaks = (600.0, 545.0, 445.0)  # X, Y, Z peak wavelengths
    sigmas = (38.0, 42.0, 36.0)
    basis = []
    for wl in wavelengths_nm:
        row = [
            math.exp(-0.5 * ((wl - mu) / s) ** 2)
            for mu, s in zip(peaks, sigmas)
        ]
        basis.append(row)
    return basis


def resolve_basis(config: dict):
    wl = [float(w) for w in config["wavelengths"]]
    basis = config.get("xyz_basis")
    if basis is None:
        basis = synthetic_xyz_basis(wl)
    else:
        if len(basis) != len(wl):
            raise ValueError("xyz_basis length must match wavelengths")
        basis = [[float(v) for v in row] for row in basis]
    spacing = wavelength_spacing_weights(wl)
    return wl, basis, spacing


def xyz_to_linear_srgb(xyz: Sequence[float],
                       white: Sequence[float] = (1.0, 1.0, 1.0)):
    """Map linear XYZ to linear sRGB with an optional reference white.

    The standard D65 matrix already maps the D65 white point to (1, 1, 1),
    so the default white is a no-op scale.  Configs using a custom observer
    basis may pass the XYZ of their reference white spectrum instead.
    """
    xw, yw, zw = white
    x, y, z = xyz
    if yw:
        x, y, z = x / xw * yw, y, z / zw * yw
    out = []
    for row in _XYZ_TO_SRGB:
        v = row[0] * x + row[1] * y + row[2] * z
        out.append(v)
    return out


def encode_srgb(linear: Sequence[float]) -> List[float]:
    """Apply the sRGB opto-electronic transfer function per channel."""
    out = []
    for v in linear:
        if v <= 0.0:
            out.append(0.0)
        elif v < 0.0031308:
            out.append(12.92 * v)
        else:
            out.append(1.055 * (v ** (1.0 / 2.4)) - 0.055)
    return out


def clip01(values: Sequence[float]) -> List[float]:
    return [0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v)) for v in values]


def linear_srgb_to_display(linear: Sequence[float], exposure: float = 1.0):
    scaled = [v * exposure for v in linear]
    return clip01(encode_srgb(scaled))
