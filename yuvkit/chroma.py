"""Chroma sub-sampling siting and phase conventions.

Two independent conventions have to be pinned down before a 4:2:0/4:2:2
round trip can be called lossless "within tolerance":

*Siting* - horizontal phase of the chroma grid relative to the luma
grid.  ``left`` (a.k.a. co-sited, MPEG-2 horizontal phase 0) puts Cb/Cr
on the same column as a luma sample, while ``center`` (ITU-R BT.601,
JPEG, H.264 "center") puts it half a luma sample to the right of the
first column it covers.  For the layouts in scope, 4:2:0 is also
vertically centred in ``center`` mode and vertically co-sited in
``left`` mode (top-field conventions).

*Phase* - the numeric offset implied by the siting, and with it the
reconstruction filter.  A ``center`` grid reconstructs by linear
interpolation with weights 1/4 and 3/4 (a 0.5-sample phase shift); a
``left`` grid replicates / linearly blends a full sample to the right
(phase 0).

The forward sub-sampling and its inverse live in the same class so an
encode/decode pair can never disagree about the phase: a mismatch of
half a sample is exactly the class of bug that shows up as chroma
fringing and "high saturation colour shift" after delivery.  The
``center`` pair is designed to reproduce a linear ramp exactly (the
box average followed by the 1/4-3/4 blend is an identity on
``v[x] = a*x + b``), which is the property the banding test relies on.
"""

from fractions import Fraction

from .ranges import quantise, round_half_up

__all__ = [
    "ChromaLayout",
    "Siting",
    "LAYOUTS",
    "get_layout",
    "downsample",
    "upsample",
    "planar_to_420",
    "planar_420_to_full",
    "planar_to_422",
    "planar_422_to_full",
]

_ZERO = Fraction(0)
_ONE = Fraction(1)
_HALF = Fraction(1, 2)


class Siting:
    """One chroma siting convention."""

    __slots__ = ("name", "h_phase", "v_phase")

    def __init__(self, name, h_phase, v_phase):
        self.name = name
        self.h_phase = Fraction(h_phase)   # in luma samples: 0 or 1/2
        self.v_phase = Fraction(v_phase)

    def describe(self):
        return {
            "name": self.name,
            "h_phase": float(self.h_phase),
            "v_phase": float(self.v_phase),
        }

    def __repr__(self):
        return "Siting(%r, h=%s, v=%s)" % (self.name, self.h_phase, self.v_phase)


LEFT = Siting("left", _ZERO, _ZERO)
CENTER = Siting("center", _HALF, _HALF)

_SITING_ALIASES = {
    "left": LEFT,
    "co-sited": LEFT,
    "cosited": LEFT,
    "mpeg2": LEFT,
    "dv": LEFT,
    "center": CENTER,
    "centre": CENTER,
    "centered": CENTER,
    "centred": CENTER,
    "jpeg": CENTER,
    "bt601": CENTER,
}


class ChromaLayout:
    """A sub-sampling layout: 4:4:4, 4:2:2 or 4:2:0."""

    __slots__ = ("name", "h_sub", "v_sub", "siting")

    def __init__(self, name, h_sub, v_sub, siting):
        self.name = name
        self.h_sub = h_sub
        self.v_sub = v_sub
        self.siting = siting

    @property
    def chroma_phase(self):
        """Phase offset of the reconstructed chroma, in *luma* samples.

        Zero for 4:4:4 (no sub-sampling) and for ``left`` siting; one half
        for ``center`` siting, where the chroma grid sits halfway between
        the two luma columns (or rows) it covers.  The same number drives
        :func:`downsample` and :func:`upsample`, which is what makes the
        pair invertible on linear ramps.
        """
        h = self.siting.h_phase if self.h_sub > 1 else _ZERO
        v = self.siting.v_phase if self.v_sub > 1 else _ZERO
        return (h, v)

    def describe(self):
        return {
            "name": self.name,
            "h_sub": self.h_sub,
            "v_sub": self.v_sub,
            "siting": self.siting.describe(),
            "phase": [float(self.chroma_phase[0]), float(self.chroma_phase[1])],
        }

    def __repr__(self):
        return "ChromaLayout(%r, %dx%d, %r)" % (
            self.name, self.h_sub, self.v_sub, self.siting.name)


LAYOUTS = {
    "444": ChromaLayout("4:4:4", 1, 1, CENTER),
    "422": ChromaLayout("4:2:2", 2, 1, CENTER),
    "420": ChromaLayout("4:2:0", 2, 2, CENTER),
    "422left": ChromaLayout("4:2:2-left", 2, 1, LEFT),
    "420left": ChromaLayout("4:2:0-left", 2, 2, LEFT),
}


def get_layout(name):
    """Return a :class:`ChromaLayout` by name, alias or siting."""
    if isinstance(name, ChromaLayout):
        return name
    key = str(name).strip().lower()
    for sep in (":", "_", "-", " "):
        key = key.replace(sep, "")
    key = key.replace("yuv", "")
    if key in _SITING_ALIASES:
        return ChromaLayout("4:2:0 " + key, 2, 2, _SITING_ALIASES[key])
    if key not in LAYOUTS:
        raise ValueError(
            "unknown chroma layout %r (expected 4:4:4, 4:2:2, 4:2:0, "
            "4:2:2-left, 4:2:0-left, or a siting name)" % (name,))
    return LAYOUTS[key]


# ----------------------------------------------------------------------
# 1-D resampling pair (exact rationals, quantised by the caller)
# ----------------------------------------------------------------------
def _down_1d(values, phase):
    """Average pairs of *values*.

    The box average is the correct analysis filter for both sitings in
    scope: the phase decides where the sub-sampled sample *sits* (and
    therefore how it is reconstructed), not which luma pixels it
    integrates.  Keeping the analysis filter identical for both sitings
    is what lets the two conventions be compared frame to frame.
    """
    out = []
    for i in range(0, len(values), 2):
        if i + 1 < len(values):
            out.append((Fraction(values[i]) + Fraction(values[i + 1])) * _HALF)
        else:                                   # odd length: replicate edge
            out.append(Fraction(values[i]))
    return out


def _up_1d(values, length, phase):
    """Reconstruct *length* samples from the half-rate *values*.

    Sample ``j`` of ``values`` sits at luma position ``2j + phase``; a
    luma position ``x`` is reconstructed by linear interpolation between
    the two surrounding chroma samples.  Outside the chroma grid the
    interpolation is *extrapolated* along the first/last pair rather than
    replicated: replication would flatten the first and last half sample
    of every row, which is a visible band at the image edge and breaks
    the "a linear ramp comes back unchanged" property this filter pair is
    designed around.
    """
    n = len(values)
    if n == 0:
        return [Fraction(0)] * length
    vals = [Fraction(v) for v in values]
    if n == 1:
        return [vals[0]] * length
    out = []
    for x in range(length):
        u = (Fraction(x) - phase) * _HALF   # position in chroma index space
        j = u.numerator // u.denominator    # floor without float rounding
        frac = u - j
        if j < 0:
            lo, hi, f = vals[0], vals[1], u
        elif j >= n - 1:
            lo, hi, f = vals[n - 2], vals[n - 1], u - (n - 2)
        else:
            lo, hi, f = vals[j], vals[j + 1], frac
        out.append(lo + (hi - lo) * f)
    return out


def downsample(plane, layout, axis):
    """Sub-sample one chroma plane along *axis* (``"h"`` or ``"v"``)."""
    form = get_layout(layout)
    if axis == "h":
        step = form.h_sub
        rows = [([Fraction(c) for c in row]) for row in plane]
        widths = len(plane[0]) if plane else 0
    elif axis == "v":
        step = form.v_sub
        rows = [[Fraction(c) for c in row] for row in plane]
        widths = len(plane[0]) if plane else 0
    else:
        raise ValueError("axis must be 'h' or 'v'")
    if step == 1:
        return [[_to_int(v) for v in row] for row in rows]
    phase = form.siting.h_phase if axis == "h" else form.siting.v_phase
    if axis == "h":
        return [[_to_int(v) for v in _down_1d(row, phase)] for row in rows]
    out = []
    for y in range(0, len(rows) - 1, 2):
        a, b = rows[y], rows[y + 1]
        out.append([_to_int((a[x] + b[x]) * _HALF) for x in range(widths)])
    if len(rows) % 2:
        out.append([_to_int(v) for v in rows[-1]])
    return out


def upsample(plane, layout, axis, length):
    """Inverse of :func:`downsample` (round trip is exact on ramps)."""
    form = get_layout(layout)
    if axis == "h":
        step = form.h_sub
        if step == 1:
            return [[_to_int(v) for v in row] for row in plane]
        phase = form.siting.h_phase
        out = []
        for row in plane:
            vals = [Fraction(v) for v in row]
            out.append([_to_int(v) for v in _up_1d(vals, length, phase)])
        return out
    step = form.v_sub
    if step == 1:
        return [[_to_int(v) for v in row] for row in plane]
    if axis != "v":
        raise ValueError("axis must be 'h' or 'v'")
    phase = form.siting.v_phase
    cols = len(plane[0]) if plane else 0
    # build the vertical vectors, resample each, transpose back
    vecs = [[Fraction(plane[y][x]) for y in range(len(plane))] for x in range(cols)]
    up = [_up_1d(v, length, phase) for v in vecs]
    return [[_to_int(up[x][y]) for x in range(cols)] for y in range(length)]


def _to_int(value):
    """Round an exact rational half-up to the nearest integer code."""
    return round_half_up(Fraction(value))


# ----------------------------------------------------------------------
# convenience wrappers for Cb/Cr pairs
# ----------------------------------------------------------------------
def planar_to_420(cb, cr, siting="center"):
    """Sub-sample full resolution Cb/Cr planes to 4:2:0."""
    form = get_layout(siting)
    cb_h = downsample(cb, form, "h")
    cr_h = downsample(cr, form, "h")
    return downsample(cb_h, form, "v"), downsample(cr_h, form, "v"), form


def planar_420_to_full(cb, cr, width, height, siting="center"):
    """Reconstruct full resolution Cb/Cr planes from 4:2:0."""
    form = get_layout(siting)
    cb_v = upsample(cb, form, "v", height)
    cr_v = upsample(cr, form, "v", height)
    return (upsample(cb_v, form, "h", width),
            upsample(cr_v, form, "h", width), form)


def planar_to_422(cb, cr, siting="center"):
    """Sub-sample full resolution Cb/Cr planes to 4:2:2."""
    form = get_layout(siting)
    return downsample(cb, form, "h"), downsample(cr, form, "h"), form


def planar_422_to_full(cb, cr, width, height, siting="center"):
    """Reconstruct full resolution Cb/Cr planes from 4:2:2."""
    form = get_layout(siting)
    return (upsample(cb, form, "h", width),
            upsample(cr, form, "h", width), form)


