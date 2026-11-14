"""High level conversion API: pixels in, pixels + provenance out.

The conversion order implemented here is the one the standard prescribes:

    RGB code  --normalise by its own range-->  R'G'B' 0..1
              --colour matrix--------------->  Y' 0..1, Cb/Cr -0.5..0.5
              --scale by the *YUV* range---->  Y'CbCr
              --round half up, clip--------->  codes

The reverse direction does the exact mirror image.  Range scaling is
*never* applied before the matrix, and never to the wrong plane: the
original defect scaled the YUV end of an RGB->YUV copy before the
matrix, which both shifted the level (washed out blacks) and compressed
chroma around the wrong centre (grey-ish, tinted saturated colour).

Two interchangeable engines are available.  ``"matrix"`` evaluates the
rational matrix expressions directly; ``"lut"`` uses precomputed
per-channel integer tables.  Both are exact, so they must agree
byte-for-byte; :func:`compare_paths` enforces that and is what the
acceptance test for "different implementation paths agree" runs.
"""

from fractions import Fraction

from .chroma import get_layout
from .lut import get_forward_lut, get_inverse_lut
from .matrix import RawCode, get_matrix
from .ranges import clip, get_range
from .standards import get_standard
from .trace import RangeTags, frac_to_json

__all__ = [
    "ConversionError",
    "Converter",
    "PixelResult",
    "compare_paths",
    "compare_all",
    "EPSILON",
]

#: Round-trip budget in 8-bit code units, expressed as a fraction of 255.
EPSILON = Fraction(2, 255)

_PATHS = ("matrix", "lut")


class ConversionError(ValueError):
    """Raised for malformed pixels or unsupported option combinations."""


class PixelResult:
    """One converted pixel: codes, provenance and optional diagnostics."""

    __slots__ = ("codes", "raw", "clipped", "tags", "notes")

    def __init__(self, codes, raw, clipped, tags, notes=None):
        self.codes = codes
        self.raw = raw            # tuple of RawCode (unrounded)
        self.clipped = clipped    # tuple of bools
        self.tags = tags          # RangeTags
        self.notes = notes or []

    def __iter__(self):
        return iter(self.codes)

    def __getitem__(self, index):
        return self.codes[index]

    def __len__(self):
        return len(self.codes)

    def __repr__(self):
        return "PixelResult(%r, clipped=%r)" % (self.codes, self.clipped)

    def trace(self, index=None, source=None):
        """Flat record with everything needed to explain this pixel."""
        rec = {}
        if index is not None:
            rec["index"] = index
        if source is not None:
            rec["src"] = list(source)
        rec["dst"] = list(self.codes)
        rec["raw"] = [frac_to_json(r.fraction) for r in self.raw]
        rec["clipped"] = list(self.clipped)
        rec["tags"] = self.tags.describe()
        if self.notes:
            rec["notes"] = list(self.notes)
        return rec


class Converter:
    """Converts pixels between R'G'B' and Y'CbCr for one convention set."""

    __slots__ = ("standard", "matrix", "rgb_range", "yuv_range",
                 "layout", "path", "_fwd", "_inv", "rgb", "yuv")

    def __init__(self, standard="bt709", rgb_range="full",
                 yuv_range="limited", layout="4:4:4", path="matrix"):
        std = get_standard(standard)
        self.standard = std.name
        self.matrix = get_matrix(std)
        self.rgb = get_range(rgb_range)
        self.yuv = get_range(yuv_range)
        self.rgb_range = self.rgb.name
        self.yuv_range = self.yuv.name
        self.layout = get_layout(layout)
        if path not in _PATHS:
            raise ConversionError("path must be one of %r, got %r"
                                  % (_PATHS, path))
        self.path = path
        if path == "lut":
            self._fwd = get_forward_lut(std, self.rgb_range, self.yuv_range)
            self._inv = get_inverse_lut(std, self.yuv_range, self.rgb_range)
        else:
            self._fwd = self._inv = None

    # -- provenance -----------------------------------------------------
    def tags(self, direction, path=None):
        phase = self.layout.chroma_phase
        return RangeTags(
            standard=self.standard,
            rgb_range=self.rgb_range,
            yuv_range=self.yuv_range,
            layout=self.layout.name,
            chroma_phase=(float(phase[0]), float(phase[1])),
            path=path or self.path,
        )

    def describe(self):
        return {
            "standard": self.matrix.describe(),
            "rgb_range": self.rgb.describe(),
            "yuv_range": self.yuv.describe(),
            "chroma": self.layout.describe(),
            "path": self.path,
            "rounding": "half-up",
            "order": "normalise -> matrix -> range-scale -> quantise",
            "tolerance": float(EPSILON),
        }

    # -- validation -----------------------------------------------------
    @staticmethod
    def _as_code(value, what):
        """Coerce an integral code; floats with a fraction part are refused."""
        if isinstance(value, bool):
            raise ConversionError("%s code must be an integer, got %r"
                                  % (what, value))
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ConversionError("%s code must be an integer, got %r"
                                      % (what, value))
            return int(value)
        if isinstance(value, Fraction):
            if value.denominator != 1:
                raise ConversionError("%s code must be an integer, got %r"
                                      % (what, value))
            return int(value)
        raise ConversionError("%s code must be an integer, got %r"
                              % (what, value))

    def _check_rgb(self, pixel, clamp=False):
        if len(pixel) != 3:
            raise ConversionError("RGB pixel needs 3 components, got %r"
                                  % (pixel,))
        out, clamped = [], []
        lo, hi = self.rgb.rgb_min, self.rgb.rgb_max
        for v in pixel:
            code = self._as_code(v, "R'G'B'")
            if code < lo or code > hi:
                if not clamp:
                    raise ConversionError(
                        "R'G'B' code %r outside the %s range %d..%d"
                        % (code, self.rgb_range, lo, hi))
                clamped.append(code)
                code = clip(code, lo, hi)
            out.append(code)
        return out, clamped

    def _check_yuv(self, pixel, clamp=False):
        if len(pixel) != 3:
            raise ConversionError("YUV pixel needs 3 components, got %r"
                                  % (pixel,))
        y, cb, cr = pixel
        limits = ((y, self.yuv.y_min, self.yuv.y_max, "luma"),
                  (cb, self.yuv.c_min, self.yuv.c_max, "Cb"),
                  (cr, self.yuv.c_min, self.yuv.c_max, "Cr"))
        out, clamped = [], []
        for v, lo, hi, what in limits:
            code = self._as_code(v, what)
            if code < lo or code > hi:
                if not clamp:
                    raise ConversionError(
                        "%s code %r outside the %s YUV range %d..%d"
                        % (what, code, self.yuv_range, lo, hi))
                clamped.append(code)
                code = clip(code, lo, hi)
            out.append(code)
        return out, clamped

    # -- conversions ----------------------------------------------------
    def rgb_to_yuv(self, pixel, index=None, source=None, clamp=False):
        """Convert one R'G'B' pixel; returns :class:`PixelResult`.

        With ``clamp=True`` codes outside the declared RGB range are
        clipped instead of rejected, and reported (``input-clipped``) -
        the posture a delivery pipeline wants when the range declaration
        and the data disagree.
        """
        (r, g, b), clamped = self._check_rgb(pixel, clamp)
        if self.path == "lut":
            raw = self._fwd(r, g, b)
        else:
            raw = self.matrix.forward_raw(r, g, b, self.rgb_range,
                                          self.yuv_range)
        notes = []
        if clamped:
            notes.append("input-clipped")
        for name, item in zip(("Y", "Cb", "Cr"), raw):
            if item.fraction < 0:
                notes.append("negative-%s" % name)
            if item.clipped:
                notes.append("clipped-%s" % name)
                if item.value < 0:
                    notes.append("clipped-low-%s" % name)
                elif item.value > 255:
                    notes.append("clipped-high-%s" % name)
        return PixelResult(tuple(x.code for x in raw), raw,
                           tuple(x.clipped for x in raw),
                           self.tags("rgb-to-yuv"), notes)

    def yuv_to_rgb(self, pixel, index=None, source=None, clamp=False):
        """Convert one Y'CbCr pixel; returns :class:`PixelResult`."""
        (y, cb, cr), clamped = self._check_yuv(pixel, clamp)
        if self.path == "lut":
            raw = self._inv(y, cb, cr)
        else:
            raw = self.matrix.inverse_raw(y, cb, cr, self.yuv_range,
                                          self.rgb_range)
        notes = []
        if clamped:
            notes.append("input-clipped")
        for name, item in zip(("R", "G", "B"), raw):
            # Negative intermediates are reported even when the rounding
            # would have landed on 0 anyway: an out-of-gamut Y'CbCr
            # triple is a *content* signal, not a rounding artefact.
            if item.fraction < 0:
                notes.append("negative-%s" % name)
            if item.clipped:
                notes.append("clipped-%s" % name)
                if item.value < 0:
                    notes.append("clipped-low-%s" % name)
                elif item.value > 255:
                    notes.append("clipped-high-%s" % name)
        return PixelResult(tuple(x.code for x in raw), raw,
                           tuple(x.clipped for x in raw),
                           self.tags("yuv-to-rgb"), notes)

    # -- convenience ----------------------------------------------------
    def convert(self, pixel, direction, clamp=False):
        if direction == "rgb-to-yuv":
            return self.rgb_to_yuv(pixel, clamp=clamp)
        if direction == "yuv-to-rgb":
            return self.yuv_to_rgb(pixel, clamp=clamp)
        raise ConversionError("unknown direction %r" % (direction,))

    def to_yuv(self, rgb_pixel, other=None):
        """Convert to YUV in this convention, optionally out of *other*."""
        src = other or self
        return src.rgb_to_yuv(rgb_pixel).codes

    def roundtrip_pixel(self, pixel, direction):
        """Convert and convert back; returns (first, back, max error)."""
        first = self.convert(pixel, direction)
        back_dir = ("yuv-to-rgb" if direction == "rgb-to-yuv"
                    else "rgb-to-yuv")
        back = self.convert(first.codes, back_dir)
        err = max(abs(a - b) for a, b in zip(pixel, back.codes))
        return first, back, err

    def __repr__(self):
        return ("Converter(%r, rgb=%r, yuv=%r, layout=%r, path=%r)"
                % (self.standard, self.rgb_range, self.yuv_range,
                   self.layout.name, self.path))


# ----------------------------------------------------------------------
# cross-path agreement
# ----------------------------------------------------------------------
def compare_paths(pixels, direction="rgb-to-yuv", standard="bt709",
                  rgb_range="full", yuv_range="limited", layout="4:4:4"):
    """Convert *pixels* through both engines and characterise the delta.

    Returns a dict with ``identical`` (the only acceptable outcome for
    the built-in engines), the maximum absolute code difference, the
    number of differing pixels and, for the pixels that do differ, the
    distance of the exact value from a rounding boundary - which is how
    a genuine "one path rounds the other way" case is distinguished from
    a modelling error.
    """
    a = Converter(standard, rgb_range, yuv_range, layout, "matrix")
    b = Converter(standard, rgb_range, yuv_range, layout, "lut")
    differing = []
    max_delta = 0
    for idx, pixel in enumerate(pixels):
        pa = a.convert(pixel, direction)
        pb = b.convert(pixel, direction)
        delta = max(abs(x - y) for x, y in zip(pa.codes, pb.codes))
        max_delta = max(max_delta, delta)
        if delta:
            differing.append({
                "index": idx,
                "src": list(pixel),
                "matrix": list(pa.codes),
                "lut": list(pb.codes),
                "delta": delta,
                "boundary_distance": [
                    float(r.boundary_distance()) for r in pa.raw],
            })
    return {
        "direction": direction,
        "standard": a.standard,
        "pixels": len(pixels),
        "identical": not differing,
        "max_delta": max_delta,
        "differing": len(differing),
        "detail": differing[:64],
        "max_boundary_distance": max(
            (max(d["boundary_distance"]) for d in differing), default=0.0),
    }



def compare_all(pixels, direction="rgb-to-yuv", standard="bt709",
                rgb_range="full", yuv_range="limited", layout="4:4:4"):
    """Compare the exact path against every independent implementation.

    Engines:
      ``matrix``  the production rational path
      ``lut``     table driven integer path (must be identical)
      ``float64`` plain float matrix, inverse obtained numerically
      ``float32`` IEEE-754 binary32 evaluation, as a shader would do
      ``q16``     fixed point with a single truncation step

    For any non-zero delta the distance from the exact value to the
    nearest rounding boundary is reported.  The acceptance criterion is
    "deltas <= 1 and confined to the rounding boundary", which is exactly
    what ``all_within_one_code`` and ``max_boundary_distance`` encode.
    """
    from .reference import ReferenceConverter, FixedPointReference

    exact = Converter(standard, rgb_range, yuv_range, layout, "matrix")
    lut = Converter(standard, rgb_range, yuv_range, layout, "lut")
    ref = ReferenceConverter(exact.standard, rgb_range, yuv_range)
    fixed = FixedPointReference(exact.standard, rgb_range, yuv_range)

    engines = {
        "float64": lambda p: ref.convert(p, direction),
        "float32": lambda p: ref.convert_f32(p, direction),
        "q16": lambda p: fixed.convert(p, direction),
    }

    stats = {
        name: {"max_delta": 0, "differing": 0, "max_boundary_distance": 0.0,
               "examples": []}
        for name in ("lut",) + tuple(engines)
    }
    for idx, pixel in enumerate(pixels):
        ex = exact.convert(pixel, direction)
        base = list(ex.codes)
        candidates = {"lut": list(lut.convert(pixel, direction).codes)}
        for name, fn in engines.items():
            candidates[name] = list(fn(pixel))
        for name, got in candidates.items():
            delta = 0
            boundary = 0.0
            for i, (a, b) in enumerate(zip(base, got)):
                if a != b:
                    delta = max(delta, abs(a - b))
                    boundary = max(boundary, ex.raw[i].boundary_distance())
            if delta:
                entry = stats[name]
                entry["differing"] += 1
                entry["max_delta"] = max(entry["max_delta"], delta)
                entry["max_boundary_distance"] = max(
                    entry["max_boundary_distance"], boundary)
                if len(entry["examples"]) < 8:
                    entry["examples"].append({
                        "index": idx, "src": list(pixel),
                        "exact": base, name: got, "delta": delta,
                        "boundary_distance": boundary,
                    })
    summary = {
        "pixels": len(pixels),
        "direction": direction,
        "standard": exact.standard,
        "engines": stats,
        "all_within_one_code": all(v["max_delta"] <= 1
                                   for v in stats.values()),
        "all_identical": all(v["differing"] == 0 for v in stats.values()),
        "max_delta": max((v["max_delta"] for v in stats.values()),
                         default=0),
    }
    return summary
