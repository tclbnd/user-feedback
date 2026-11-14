"""Exact colour matrices (integer/rational, no floating point).

Why exact integers: the two implementation paths (a table driven LUT and
a direct matrix multiply) must agree *bit for bit*, and RGB->YUV->RGB
must stay inside a 2/255 tolerance for saturated primaries.  Floating
point leaves both properties at the mercy of the host FPU, so every
constant is kept as an exact integer ratio and all intermediates are
exact rationals.  The only lossy step is the final quantisation.

Units
-----
Everything is expressed on the *legal* (normalised) scale:

    R', G', B', Y'   0 .. 1                (0 maps to ``*_min``)
    Cb, Cr           -1/2 .. +1/2          (0 maps to the chroma zero code)

The matrix therefore never sees a range-scaled value.  On the way in,
range normalisation happens *before* the matrix (a code is only ever
divided by its own span); on the way out, range scaling happens
*strictly after* the matrix.  The original bug was the exact opposite
order, applied to the YUV side of an RGB->YUV conversion.

Constants
---------
With ``DEN = 10000`` the weights are ``Kr = krn/DEN`` etc. and the
chroma gains are derived from the *same* Kr/Kb as the luma:

    Kr + Kg + Kb = 1
    Cb = (B' - Y') / (2 * (1 - Kb))
    Cr = (R' - Y') / (2 * (1 - Kr))

:meth:`ColorMatrix.validate` asserts those identities, so a table that
silently borrows BT.601 weights for a BT.709 path cannot slip through:
the constants are checked against the standard's own definition, not
against a copy of some other table.
"""

from fractions import Fraction

from .ranges import get_range
from .standards import get_standard

__all__ = [
    "DEN",
    "RawCode",
    "ColorMatrix",
    "get_matrix",
    "MATRICES",
    "MixedStandardError",
    "quantise_ratio",
]

DEN = 10000


class MixedStandardError(ValueError):
    """Raised when constants from two generations of a standard are mixed."""


class RawCode:
    """An unrounded target code held as an exact rational ``num/den``."""

    __slots__ = ("num", "den", "code", "clipped")

    def __init__(self, num, den, lo, hi):
        if den < 0:
            num, den = -num, -den
        self.num = num
        self.den = den
        rounded = quantise_ratio(num, den)
        if rounded < lo:
            self.code = lo
            self.clipped = True
        elif rounded > hi:
            self.code = hi
            self.clipped = True
        else:
            self.code = rounded
            self.clipped = False

    @property
    def value(self):
        return self.num / self.den

    @property
    def fraction(self):
        return Fraction(self.num, self.den)

    def boundary_distance(self):
        """Distance from the raw value to the nearest ``n + 1/2`` boundary.

        Values within a hair of a boundary are the ones where an
        independent (floating point) implementation may legitimately
        round the other way; the cross-check test uses this to prove the
        difference between the two paths is confined to those pixels.
        """
        # Distance from raw to the nearest half integer, i.e. from
        # (raw - 1/2) to the nearest integer; done in integers so it is
        # exact even when the numerator is huge.
        p = 2 * self.num - self.den
        q = 2 * self.den
        r = p % q                       # 0 <= r < q, Python modulo
        return float(Fraction(min(r, q - r), q))

    def __repr__(self):
        return "RawCode(%r, code=%d, clipped=%r)" % (
            self.fraction, self.code, self.clipped)


def quantise_ratio(num, den):
    """Round ``num/den`` to nearest, ties toward +infinity, using integers.

    Equivalent to ``floor(num/den + 1/2)`` for ``den > 0`` but computed
    with ``//`` only, so it is exact for arbitrarily large numerators and
    identical on every platform.
    """
    if den < 0:
        num, den = -num, -den
    return (2 * num + den) // (2 * den)


class ColorMatrix:
    """Forward (R'G'B'->Y'CbCr) and inverse matrix for one standard."""

    __slots__ = ("standard", "krn", "kgn", "kbn",
                 "cb_num", "cb_den", "cr_num", "cr_den")

    def __init__(self, standard):
        std = standard if hasattr(standard, "kr") else get_standard(standard)
        self.standard = std
        self.krn = int(std.kr * DEN)
        self.kbn = int(std.kb * DEN)
        # Green weight comes from the normalisation, never from a literal.
        self.kgn = DEN - self.krn - self.kbn
        # Gains derived from the same Kr / Kb.
        self.cb_num = DEN
        self.cb_den = 2 * (DEN - self.kbn)
        self.cr_num = DEN
        self.cr_den = 2 * (DEN - self.krn)
        self.validate()

    # -- auditing -------------------------------------------------------
    def validate(self):
        """Assert the constants are self-consistent (and belong together)."""
        std = self.standard
        if Fraction(self.krn, DEN) != std.kr or Fraction(self.kbn, DEN) != std.kb:
            raise MixedStandardError(
                "%s: matrix weights %d/%d do not represent the standard"
                % (std.name, self.krn, self.kbn))
        if self.krn + self.kgn + self.kbn != DEN:
            raise MixedStandardError(
                "%s: Kr+Kg+Kb != 1 (%d+%d+%d != %d)"
                % (std.name, self.krn, self.kgn, self.kbn, DEN))
        gain_cb = Fraction(self.cb_num, self.cb_den)
        if gain_cb != std.cb_gain:
            raise MixedStandardError(
                "%s: Cb gain %r != 1/(2*(1-Kb)) = %r"
                % (std.name, gain_cb, std.cb_gain))
        gain_cr = Fraction(self.cr_num, self.cr_den)
        if gain_cr != std.cr_gain:
            raise MixedStandardError(
                "%s: Cr gain %r != 1/(2*(1-Kr)) = %r"
                % (std.name, gain_cr, std.cr_gain))
        if self.kgn <= 0:
            raise MixedStandardError("%s: non-positive Kg" % std.name)
        return True

    def describe(self):
        return {
            "standard": self.standard.name,
            "kr": self.krn / DEN,
            "kg": self.kgn / DEN,
            "kb": self.kbn / DEN,
            "cb_gain": self.cb_num / self.cb_den,
            "cr_gain": self.cr_num / self.cr_den,
            "den": DEN,
        }

    # -- forward: R'G'B' codes -> Y'CbCr codes -------------------------
    def forward_raw(self, r, g, b, rgb_range="full", yuv_range="limited"):
        """Unrounded (Y', Cb, Cr) as :class:`RawCode` objects."""
        rgb = get_range(rgb_range)
        yuv = get_range(yuv_range)
        span = rgb.rgb_max - rgb.rgb_min
        rn, gn, bn = r - rgb.rgb_min, g - rgb.rgb_min, b - rgb.rgb_min
        y_den = DEN * span
        y_num = self.krn * rn + self.kgn * gn + self.kbn * bn
        # Y' code = (Y' * y_span) + y_min
        y = RawCode(y_num * yuv.y_span + yuv.y_min * y_den, y_den,
                    yuv.y_min, yuv.y_max)
        cb = self._chroma(DEN * bn - y_num, y_den, self.cb_num, self.cb_den, yuv)
        cr = self._chroma(DEN * rn - y_num, y_den, self.cr_num, self.cr_den, yuv)
        return y, cb, cr

    @staticmethod
    def _chroma(diff_num, norm_den, gain_num, gain_den, yuv):
        """C code = (diff/norm_den) * gain * c_span + c_zero."""
        num = diff_num * gain_num * yuv.c_span + yuv.c_zero * norm_den * gain_den
        den = norm_den * gain_den
        return RawCode(num, den, yuv.c_min, yuv.c_max)

    # -- inverse: Y'CbCr codes -> R'G'B' codes -------------------------
    def inverse_raw(self, y_code, cb_code, cr_code, yuv_range="limited",
                    rgb_range="full"):
        """Unrounded (R', G', B') as :class:`RawCode` objects."""
        yuv = get_range(yuv_range)
        rgb = get_range(rgb_range)
        yn = y_code - yuv.y_min
        yd = yuv.y_span
        cbn = cb_code - yuv.c_zero
        crn = cr_code - yuv.c_zero
        cd = yuv.c_span
        # R' = Y' + 2*(1-Kr)*Cr ; B' = Y' + 2*(1-Kb)*Cb
        r_num = yn * DEN * cd + 2 * (DEN - self.krn) * crn * yd
        r_den = yd * DEN * cd
        b_num = yn * DEN * cd + 2 * (DEN - self.kbn) * cbn * yd
        b_den = yd * DEN * cd
        # G' = (Y' - Kr*R' - Kb*B') / Kg
        g_num = DEN * (yn * DEN * r_den * b_den
                       - self.krn * r_num * yd * b_den
                       - self.kbn * b_num * yd * r_den)
        g_den = yd * DEN * r_den * b_den * self.kgn
        span = rgb.rgb_max - rgb.rgb_min
        lo, hi = rgb.rgb_min, rgb.rgb_max
        return (
            RawCode(r_num * span + lo * r_den, r_den, lo, hi),
            RawCode(g_num * span + lo * g_den, g_den, lo, hi),
            RawCode(b_num * span + lo * b_den, b_den, lo, hi),
        )


def get_matrix(standard):
    """Return the cached :class:`ColorMatrix` for a standard or its name."""
    if isinstance(standard, ColorMatrix):
        return standard
    return MATRICES[get_standard(standard).name]


MATRICES = {name: ColorMatrix(name) for name in ("bt601", "bt709", "bt2020")}

