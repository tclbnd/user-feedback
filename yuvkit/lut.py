"""Table driven (LUT) implementation path.

Same constants as :mod:`yuvkit.matrix`, different mechanism: instead of
composing the rational expressions symbolically, every dependency on a
single input code is precomputed as an integer numerator over one fixed
denominator per direction.  A pixel is then three integer additions and
one rounding - the classic codec implementation - which is useful both
for speed and as an independent path to cross-check the matrix code
against.

Both paths are exact, so "consistent" means *identical*, not merely
close; :func:`yuvkit.converter.compare_paths` verifies that on any pixel
set.  The tables are built from the matrix object itself (not from a
copy of its constants), which is what keeps them in step when a
constant is corrected.
"""

from .matrix import DEN, RawCode, get_matrix
from .ranges import get_range

__all__ = ["ForwardLUT", "InverseLUT", "get_forward_lut", "get_inverse_lut"]


class ForwardLUT:
    """Per-channel tables for R'G'B' -> Y'CbCr."""

    __slots__ = ("standard", "rgb_range", "yuv_range", "_lo", "_hi",
                 "y_den", "y_r", "y_g", "y_b", "y_const",
                 "cb_den", "cb_r", "cb_g", "cb_b", "cb_const",
                 "cr_den", "cr_r", "cr_g", "cr_b", "cr_const")

    def __init__(self, standard, rgb_range="full", yuv_range="limited"):
        m = get_matrix(standard)
        rgb = get_range(rgb_range)
        yuv = get_range(yuv_range)
        self.standard = m.standard.name
        self.rgb_range = rgb.name
        self.yuv_range = yuv.name
        span = rgb.rgb_max - rgb.rgb_min
        k = range(rgb.rgb_min, rgb.rgb_max + 1)
        off = rgb.rgb_min
        # -- luma -------------------------------------------------------
        # y_num = sum(krn*rn) * y_span + y_min * DEN * span
        self.y_den = DEN * span
        self.y_r = [m.krn * yuv.y_span * (c - off) for c in k]
        self.y_g = [m.kgn * yuv.y_span * (c - off) for c in k]
        self.y_b = [m.kbn * yuv.y_span * (c - off) for c in k]
        self.y_const = yuv.y_min * self.y_den
        # -- chroma -----------------------------------------------------
        # num = (DEN*bn - y_num) * gain_num * c_span
        #       + c_zero * y_den * gain_den
        self.cb_den = self.y_den * m.cb_den
        self.cb_r = [-m.cb_num * yuv.c_span * m.krn * (c - off) for c in k]
        self.cb_g = [-m.cb_num * yuv.c_span * m.kgn * (c - off) for c in k]
        self.cb_b = [m.cb_num * yuv.c_span * (DEN - m.kbn) * (c - off)
                     for c in k]
        self.cb_const = yuv.c_zero * self.y_den * m.cb_den
        self.cr_den = self.y_den * m.cr_den
        self.cr_r = [m.cr_num * yuv.c_span * (DEN - m.krn) * (c - off)
                     for c in k]
        self.cr_g = [-m.cr_num * yuv.c_span * m.kgn * (c - off) for c in k]
        self.cr_b = [-m.cr_num * yuv.c_span * m.kbn * (c - off) for c in k]
        self.cr_const = yuv.c_zero * self.y_den * m.cr_den
        self._lo = rgb.rgb_min
        self._hi = rgb.rgb_max

    def __call__(self, r, g, b):
        """Return unrounded (Y, Cb, Cr) as :class:`RawCode` triples."""
        lo, hi = self._lo, self._hi
        if r < lo or r > hi or g < lo or g > hi or b < lo or b > hi:
            raise ValueError("RGB code outside declared range")
        i, j, k = r - lo, g - lo, b - lo
        yuv_y = get_range(self.yuv_range)
        y = RawCode(self.y_r[i] + self.y_g[j] + self.y_b[k] + self.y_const,
                    self.y_den, yuv_y.y_min, yuv_y.y_max)
        cb = RawCode(self.cb_r[i] + self.cb_g[j] + self.cb_b[k] + self.cb_const,
                     self.cb_den, yuv_y.c_min, yuv_y.c_max)
        cr = RawCode(self.cr_r[i] + self.cr_g[j] + self.cr_b[k] + self.cr_const,
                     self.cr_den, yuv_y.c_min, yuv_y.c_max)
        return y, cb, cr

    def describe(self):
        return {
            "path": "lut",
            "direction": "rgb-to-yuv",
            "standard": self.standard,
            "rgb_range": self.rgb_range,
            "yuv_range": self.yuv_range,
            "table_bits": 8,
            "entries": len(self.y_r),
            "denominators": [self.y_den, self.cb_den, self.cr_den],
        }


class InverseLUT:
    """Per-channel tables for Y'CbCr -> R'G'B'."""

    __slots__ = ("standard", "yuv_range", "rgb_range",
                 "_y_lo", "_y_hi", "_c_lo", "_c_hi", "_rgb_lo", "_rgb_hi",
                 "r_den", "r_y", "r_cr", "r_const",
                 "g_den", "g_y", "g_cb", "g_cr", "g_const",
                 "b_den", "b_y", "b_cb", "b_const")

    def __init__(self, standard, yuv_range="limited", rgb_range="full"):
        m = get_matrix(standard)
        yuv = get_range(yuv_range)
        rgb = get_range(rgb_range)
        self.standard = m.standard.name
        self.yuv_range = yuv.name
        self.rgb_range = rgb.name
        span = rgb.rgb_max - rgb.rgb_min
        ys = range(yuv.y_min, yuv.y_max + 1)
        cs = range(yuv.c_min, yuv.c_max + 1)
        yoff, czero = yuv.y_min, yuv.c_zero
        yd, cd = yuv.y_span, yuv.c_span
        # R' = Y' + 2(1-Kr)Cr ; code = R'*span + rgb_min
        self.r_den = yd * DEN * cd
        self.r_y = [(c - yoff) * DEN * cd * span for c in ys]
        self.r_cr = [2 * (DEN - m.krn) * (c - czero) * yd * span for c in cs]
        self.r_const = rgb.rgb_min * self.r_den
        self.b_den = yd * DEN * cd
        self.b_y = [(c - yoff) * DEN * cd * span for c in ys]
        self.b_cb = [2 * (DEN - m.kbn) * (c - czero) * yd * span for c in cs]
        self.b_const = rgb.rgb_min * self.b_den
        # G' = Y' - 2*Kr*(1-Kr)/Kg * Cr - 2*Kb*(1-Kb)/Kg * Cb
        self.g_den = yd * DEN * cd * m.kgn
        self.g_y = [(c - yoff) * DEN * cd * m.kgn * span for c in ys]
        self.g_cr = [-2 * m.krn * (DEN - m.krn) * (c - czero) * yd * span
                     for c in cs]
        self.g_cb = [-2 * m.kbn * (DEN - m.kbn) * (c - czero) * yd * span
                     for c in cs]
        self.g_const = rgb.rgb_min * self.g_den
        self._y_lo, self._y_hi = yuv.y_min, yuv.y_max
        self._c_lo, self._c_hi = yuv.c_min, yuv.c_max
        self._rgb_lo, self._rgb_hi = rgb.rgb_min, rgb.rgb_max

    def __call__(self, y, cb, cr):
        """Return unrounded (R', G', B') as :class:`RawCode` triples."""
        if not (self._y_lo <= y <= self._y_hi):
            raise ValueError("luma code outside declared range")
        if not (self._c_lo <= cb <= self._c_hi
                and self._c_lo <= cr <= self._c_hi):
            raise ValueError("chroma code outside declared range")
        yi = y - self._y_lo
        ci = cb - self._c_lo
        cj = cr - self._c_lo
        lo, hi = self._rgb_lo, self._rgb_hi
        return (
            RawCode(self.r_y[yi] + self.r_cr[cj] + self.r_const,
                    self.r_den, lo, hi),
            RawCode(self.g_y[yi] + self.g_cb[ci] + self.g_cr[cj] + self.g_const,
                    self.g_den, lo, hi),
            RawCode(self.b_y[yi] + self.b_cb[ci] + self.b_const,
                    self.b_den, lo, hi),
        )

    def describe(self):
        return {
            "path": "lut",
            "direction": "yuv-to-rgb",
            "standard": self.standard,
            "yuv_range": self.yuv_range,
            "rgb_range": self.rgb_range,
            "table_bits": 8,
            "entries": [len(self.r_y), len(self.r_cr)],
            "denominators": [self.r_den, self.g_den, self.b_den],
        }


_FORWARD_CACHE = {}
_INVERSE_CACHE = {}


def get_forward_lut(standard, rgb_range="full", yuv_range="limited"):
    key = (get_matrix(standard).standard.name, get_range(rgb_range).name,
           get_range(yuv_range).name)
    if key not in _FORWARD_CACHE:
        _FORWARD_CACHE[key] = ForwardLUT(*key)
    return _FORWARD_CACHE[key]


def get_inverse_lut(standard, yuv_range="limited", rgb_range="full"):
    key = (get_matrix(standard).standard.name, get_range(yuv_range).name,
           get_range(rgb_range).name)
    if key not in _INVERSE_CACHE:
        _INVERSE_CACHE[key] = InverseLUT(*key)
    return _INVERSE_CACHE[key]

