"""Sample ranges, quantisation and clipping rules.

There are exactly two 8-bit conventions in play:

``full``      Y' 0..255, Cb/Cr 0..255 (zero chroma at 128), R'G'B' 0..255
``limited``   Y' 16..235, Cb/Cr 16..240 (zero chroma at 128), R'G'B' 16..235

Everything internal is kept as an exact rational on the normalised
*legal* scale, i.e. ``0 <= v <= 1`` for luma/primary values and
``-0.5 <= c <= +0.5`` for chroma.  Only the final quantisation step
turns these into codes, and only after the colour matrix has been
applied - scaling the range before the matrix (or scaling the YUV end
of an RGB->YUV copy) is precisely the ordering bug this module fixes.
"""

from fractions import Fraction

import math

__all__ = [
    "RangeError",
    "SampleRange",
    "FULL",
    "LIMITED",
    "RANGES",
    "get_range",
    "canonical_range",
    "round_half_up",
    "quantise",
    "clip",
    "Quantised",
]

_ONE = Fraction(1)
_HALF = Fraction(1, 2)


class RangeError(ValueError):
    """Raised for an unknown range name or an impossible code value."""


class SampleRange:
    """Code value layout of one range convention.

    ``y_span``/``c_span`` are the spans used for normalisation, both are
    expressed in the same 1/255 units as the 8-bit code, which keeps the
    chroma scaling independent of the luma scaling when 4:2:0 sub-samples
    are involved.
    """

    __slots__ = (
        "name",
        "y_min", "y_max",
        "c_min", "c_max",
        "rgb_min", "rgb_max",
        "y_span", "c_span", "c_zero",
    )

    def __init__(self, name, y_min, y_max, c_min, c_max, rgb_min, rgb_max,
                 c_zero=128):
        self.name = name
        self.y_min = y_min
        self.y_max = y_max
        self.c_min = c_min
        self.c_max = c_max
        self.rgb_min = rgb_min
        self.rgb_max = rgb_max
        self.y_span = y_max - y_min
        self.c_span = c_max - c_min
        self.c_zero = c_zero

    # -- luma / primary normalisation -----------------------------------
    def y_to_norm(self, code):
        """Map an integer Y' code to the normalised legal luma scale."""
        return Fraction(code - self.y_min, self.y_span)

    def norm_to_y(self, value):
        """Map normalised luma back to a (possibly fractional) code."""
        return value * self.y_span + self.y_min

    def c_to_norm(self, code):
        """Map an integer chroma code to -0.5 .. +0.5."""
        return Fraction(code - self.c_zero, self.c_span)

    def norm_to_c(self, value):
        """Map -0.5 .. +0.5 back to a (possibly fractional) code."""
        return value * self.c_span + self.c_zero

    def rgb_to_norm(self, code):
        return Fraction(code - self.rgb_min, self.rgb_max - self.rgb_min)

    def norm_to_rgb(self, value):
        return value * (self.rgb_max - self.rgb_min) + self.rgb_min

    # -- validity -------------------------------------------------------
    def y_in_range(self, code):
        return self.y_min <= code <= self.y_max

    def c_in_range(self, code):
        return self.c_min <= code <= self.c_max

    def rgb_in_range(self, code):
        return self.rgb_min <= code <= self.rgb_max

    @property
    def legal_lo(self):
        """Lowest legal code for any component of this range."""
        return min(self.y_min, self.c_min, self.rgb_min)

    @property
    def legal_hi(self):
        """Highest legal code for any component of this range."""
        return max(self.y_max, self.c_max, self.rgb_max)

    def describe(self):
        return {
            "name": self.name,
            "y": [self.y_min, self.y_max],
            "cbcr": [self.c_min, self.c_max],
            "rgb": [self.rgb_min, self.rgb_max],
            "chroma_zero": self.c_zero,
        }

    def __repr__(self):
        return (
            "SampleRange(%r, Y=%d..%d, C=%d..%d, RGB=%d..%d)"
            % (self.name, self.y_min, self.y_max,
               self.c_min, self.c_max, self.rgb_min, self.rgb_max)
        )


FULL = SampleRange("full", 0, 255, 0, 255, 0, 255)
LIMITED = SampleRange("limited", 16, 235, 16, 240, 16, 235)

RANGES = {"full": FULL, "limited": LIMITED}


def canonical_range(name):
    """Normalise a user supplied range name."""
    if isinstance(name, SampleRange):
        return name.name
    if not isinstance(name, str):
        raise RangeError("range must be a string, got %r" % (name,))
    key = name.strip().lower()
    aliases = {
        "full": "full", "pc": "full", "jpeg": "full", "f": "full",
        "0-255": "full", "255": "full",
        "limited": "limited", "tv": "limited", "video": "limited",
        "mpeg": "limited", "l": "limited", "16-235": "limited",
        "studio": "limited", "narrow": "limited",
    }
    if key not in aliases:
        raise RangeError("unknown range %r (expected 'full' or 'limited')"
                         % (name,))
    return aliases[key]


def get_range(name):
    """Return the :class:`SampleRange` singleton for *name*."""
    return RANGES[canonical_range(name)]


# ----------------------------------------------------------------------
# quantisation helpers
# ----------------------------------------------------------------------
def round_half_up(value):
    """Round an exact rational to the nearest integer, halves upward.

    ``floor(v + 1/2)`` is "nearest, ties toward +infinity" for both signs,
    which is what the sample formats expect.  Implemented on Fractions so
    there is no dependence on the host rounding mode; Python's ``round()``
    would round ties to even, which can put the LUT and matrix paths on
    different sides of a boundary.
    """
    if isinstance(value, int):
        return value
    return math.floor(Fraction(value) + _HALF)


def clip(value, lo, hi):
    """Clip *value* into ``[lo, hi]`` (used on integer codes)."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


class Quantised:
    """Result of quantising one component: the code plus diagnostics."""

    __slots__ = ("code", "raw", "clipped")

    def __init__(self, code, raw, clipped):
        self.code = code
        self.raw = raw          # unclipped, unrounded code (Fraction)
        self.clipped = clipped  # True when clipping changed the value

    def __repr__(self):
        return "Quantised(code=%d, raw=%s, clipped=%r)" % (
            self.code, float(self.raw), self.clipped)


def quantise(raw, lo, hi):
    """Round *raw* half-up and clip it into ``[lo, hi]``.

    The unrounded value is retained so the caller can report *why* a
    sample was clipped (extreme colour, negative R'G'B' intermediate).
    """
    rounded = round_half_up(raw)
    code = clip(rounded, lo, hi)
    return Quantised(code, raw, code != rounded)

