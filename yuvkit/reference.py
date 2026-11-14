"""An independent, deliberately naive float implementation.

This exists solely to be *different* from the production path: it works
in normalised floats, builds the 3x3 matrix explicitly, inverts it with
Cramer's rule, and uses ``round()``-free explicit half-up rounding.  If
the two agree except at rounding boundaries, then the exact path is not
hiding a systematic error behind its own algebra, which is exactly what
an independent cross-check is for.

It is *not* used to produce output; it is a test oracle.
"""

import math

__all__ = ["ReferenceConverter", "FixedPointReference"]

# Constants are hard-coded here on purpose - reusing yuvkit.standards
# would defeat the point of an independent check.
_TABLE = {
    "bt601": (0.299, 0.114),
    "bt709": (0.2126, 0.0722),
    "bt2020": (0.2627, 0.0593),
}

_RANGES = {
    "full": {"y": (0.0, 255.0), "c": (0.0, 255.0), "rgb": (0.0, 255.0)},
    "limited": {"y": (16.0, 235.0), "c": (16.0, 240.0), "rgb": (16.0, 235.0)},
}


def _half_up(x):
    """Nearest integer, ties toward +infinity."""
    return int(math.floor(x + 0.5))


def _clip(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


class ReferenceConverter:
    """Floating point matrix converter used as an independent oracle."""

    def __init__(self, standard="bt709", rgb_range="full",
                 yuv_range="limited"):
        self.standard = standard
        self.rgb_range = rgb_range
        self.yuv_range = yuv_range
        kr, kb = _TABLE[standard]
        kg = 1.0 - kr - kb
        self.kr, self.kg, self.kb = kr, kg, kb
        # forward matrix rows: Y, Cb, Cr
        self.fwd = [
            [kr, kg, kb],
            [-kr / (2 * (1 - kb)), -kg / (2 * (1 - kb)), 0.5],
            [0.5, -kg / (2 * (1 - kr)), -kb / (2 * (1 - kr))],
        ]
        # Inverse is obtained by numerically inverting the forward matrix
        # (Cramer's rule), not by transcribing the textbook inverse
        # formula - a transcription error would be invisible otherwise.
        self.inv = _invert3(self.fwd)

    # -- helpers --------------------------------------------------------
    def _norm_rgb(self, code):
        lo, hi = _RANGES[self.rgb_range]["rgb"]
        return (code - lo) / (hi - lo)

    def _denorm_rgb(self, value):
        lo, hi = _RANGES[self.rgb_range]["rgb"]
        return value * (hi - lo) + lo

    def _norm_y(self, code):
        lo, hi = _RANGES[self.yuv_range]["y"]
        return (code - lo) / (hi - lo)

    def _denorm_y(self, value):
        lo, hi = _RANGES[self.yuv_range]["y"]
        return value * (hi - lo) + lo

    def _norm_c(self, code):
        lo, hi = _RANGES[self.yuv_range]["c"]
        return (code - 128.0) / (hi - lo)

    def _denorm_c(self, value):
        lo, hi = _RANGES[self.yuv_range]["c"]
        return value * (hi - lo) + 128.0

    # -- conversions ----------------------------------------------------
    def rgb_to_yuv(self, pixel):
        r, g, b = (self._norm_rgb(v) for v in pixel)
        y = self.fwd[0][0] * r + self.fwd[0][1] * g + self.fwd[0][2] * b
        cb = self.fwd[1][0] * r + self.fwd[1][1] * g + self.fwd[1][2] * b
        cr = self.fwd[2][0] * r + self.fwd[2][1] * g + self.fwd[2][2] * b
        ry, rcb, rcr = self._denorm_y(y), self._denorm_c(cb), self._denorm_c(cr)
        ylo, yhi = _RANGES[self.yuv_range]["y"]
        clo, chi = _RANGES[self.yuv_range]["c"]
        return (_clip(_half_up(ry), int(ylo), int(yhi)),
                _clip(_half_up(rcb), int(clo), int(chi)),
                _clip(_half_up(rcr), int(clo), int(chi)))

    def yuv_to_rgb(self, pixel):
        y = self._norm_y(pixel[0])
        cb = self._norm_c(pixel[1])
        cr = self._norm_c(pixel[2])
        r = self.inv[0][0] * y + self.inv[0][1] * cb + self.inv[0][2] * cr
        g = self.inv[1][0] * y + self.inv[1][1] * cb + self.inv[1][2] * cr
        b = self.inv[2][0] * y + self.inv[2][1] * cb + self.inv[2][2] * cr
        rlo, rhi = _RANGES[self.rgb_range]["rgb"]
        return tuple(
            _clip(_half_up(self._denorm_rgb(v)), int(rlo), int(rhi))
            for v in (r, g, b))

    def convert(self, pixel, direction):
        if direction == "rgb-to-yuv":
            return self.rgb_to_yuv(pixel)
        return self.yuv_to_rgb(pixel)

    def convert_f32(self, pixel, direction):
        """Same conversion evaluated in IEEE-754 binary32.

        A third, genuinely independent path: codecs and shaders round
        every intermediate to single precision, which is why a shipped
        implementation can differ from an exact one by one code on
        values that sit near a rounding boundary.  Keeping this variant
        around lets the cross-check demonstrate that the delta is bounded
        by (and caused by) that boundary, not by a wrong constant.
        """
        import struct

        def f32(x):
            return struct.unpack("f", struct.pack("f", x))[0]

        if direction == "yuv-to-rgb":
            # convert the inverse by transposing through floats as well
            y = f32((pixel[0] - 16.0) / 219.0) if self.yuv_range == "limited" \
                else f32(pixel[0] / 255.0)
            cspan = 224.0 if self.yuv_range == "limited" else 255.0
            cb = f32((pixel[1] - 128.0) / cspan)
            cr = f32((pixel[2] - 128.0) / cspan)
            rows = self.inv
            vals = []
            for row in rows:
                acc = f32(0.0)
                for coeff, v in zip(row, (y, cb, cr)):
                    acc = f32(acc + f32(coeff * v))
                vals.append(acc)
            rgb_lo, rgb_hi = _RANGES[self.rgb_range]["rgb"]
            out = []
            for v in vals:
                code = f32(f32(v * (rgb_hi - rgb_lo)) + rgb_lo)
                out.append(_clip(_half_up(code), int(rgb_lo), int(rgb_hi)))
            return tuple(out)

        span = 255.0 if self.rgb_range == "full" else 219.0
        lo = 0.0 if self.rgb_range == "full" else 16.0
        r, g, b = (f32((v - lo) / span) for v in pixel)
        rows = self.fwd
        vals = []
        for row in rows:
            acc = f32(0.0)
            for coeff, v in zip(row, (r, g, b)):
                acc = f32(acc + f32(coeff * v))
            vals.append(acc)
        ylo, yhi = _RANGES[self.yuv_range]["y"]
        clo, chi = _RANGES[self.yuv_range]["c"]
        codes = []
        for index, v in enumerate(vals):
            if index == 0:
                code = f32(f32(v * (yhi - ylo)) + ylo)
                codes.append(_clip(_half_up(code), int(ylo), int(yhi)))
            else:
                code = f32(f32(v * (chi - clo)) + 128.0)
                codes.append(_clip(_half_up(code), int(clo), int(chi)))
        return tuple(codes)

    def raw_values(self, pixel, direction):
        """Unrounded float results, for boundary analysis."""
        if direction == "rgb-to-yuv":
            r, g, b = (self._norm_rgb(v) for v in pixel)
            y = self.fwd[0][0] * r + self.fwd[0][1] * g + self.fwd[0][2] * b
            cb = self.fwd[1][0] * r + self.fwd[1][1] * g + self.fwd[1][2] * b
            cr = self.fwd[2][0] * r + self.fwd[2][1] * g + self.fwd[2][2] * b
            return (self._denorm_y(y), self._denorm_c(cb), self._denorm_c(cr))
        y = self._norm_y(pixel[0])
        cb = self._norm_c(pixel[1])
        cr = self._norm_c(pixel[2])
        return (
            self._denorm_rgb(self.inv[0][0] * y + self.inv[0][1] * cb
                             + self.inv[0][2] * cr),
            self._denorm_rgb(self.inv[1][0] * y + self.inv[1][1] * cb
                             + self.inv[1][2] * cr),
            self._denorm_rgb(self.inv[2][0] * y + self.inv[2][1] * cb
                             + self.inv[2][2] * cr),
        )


def _invert3(m):
    """Invert a 3x3 float matrix with Cramer's rule."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ZeroDivisionError("singular matrix")
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


class FixedPointReference(ReferenceConverter):
    """A Q16 fixed point evaluator, the way hardware usually does it.

    Every matrix coefficient is rounded to ``frac_bits`` fractional bits
    and the accumulated products are shifted back once, so the whole
    conversion is pure integer arithmetic with a truncation step in the
    middle.  It is a *third* independent implementation, and its purpose
    is to show where a legitimate implementation difference can appear at
    all: only where the exact value sits within the fixed point
    quantisation error of a rounding boundary, and never by more than one
    code.
    """

    def __init__(self, standard="bt709", rgb_range="full",
                 yuv_range="limited", frac_bits=16):
        ReferenceConverter.__init__(self, standard, rgb_range, yuv_range)
        self.frac_bits = frac_bits
        one = 1 << frac_bits
        self.fwd_q = [[int(_half_up(q * one)) for q in row]
                      for row in self.fwd]
        self.inv_q = [[int(_half_up(q * one)) for q in row]
                      for row in self.inv]

    def _matvec(self, rows, vec):
        """Accumulate the scaled products exactly, then rescale once."""
        denom = 1 << (2 * self.frac_bits)
        return [sum(c * v for c, v in zip(row, vec)) / denom for row in rows]

    def rgb_to_yuv(self, pixel):
        span = 255.0 if self.rgb_range == "full" else 219.0
        lo = 0.0 if self.rgb_range == "full" else 16.0
        scale = 1 << self.frac_bits
        vec = [int(_half_up((v - lo) / span * scale)) for v in pixel]
        y, cb, cr = self._matvec(self.fwd_q, vec)
        ylo, yhi = _RANGES[self.yuv_range]["y"]
        clo, chi = _RANGES[self.yuv_range]["c"]
        return (_clip(_half_up(y * (yhi - ylo) + ylo), int(ylo), int(yhi)),
                _clip(_half_up(cb * (chi - clo) + 128.0), int(clo), int(chi)),
                _clip(_half_up(cr * (chi - clo) + 128.0), int(clo), int(chi)))

    def yuv_to_rgb(self, pixel):
        yspan = 219.0 if self.yuv_range == "limited" else 255.0
        ylo = 16.0 if self.yuv_range == "limited" else 0.0
        cspan = 224.0 if self.yuv_range == "limited" else 255.0
        scale = 1 << self.frac_bits
        vec = [int(_half_up((pixel[0] - ylo) / yspan * scale)),
               int(_half_up((pixel[1] - 128.0) / cspan * scale)),
               int(_half_up((pixel[2] - 128.0) / cspan * scale))]
        vals = self._matvec(self.inv_q, vec)
        rlo, rhi = _RANGES[self.rgb_range]["rgb"]
        return tuple(
            _clip(_half_up(v * (rhi - rlo) + rlo), int(rlo), int(rhi))
            for v in vals)

    def convert(self, pixel, direction):
        if direction == "rgb-to-yuv":
            return self.rgb_to_yuv(pixel)
        return self.yuv_to_rgb(pixel)
