#!/usr/bin/env python3
"""Rotation / clean-aperture / destination-canvas mapper.

Pipeline (see README.md):

    coded sample grid
      -> clean aperture           (crop in *coded* sample coordinates)
      -> display space            (divide the horizontal axis by the SAR)
      -> orientation              (clockwise rotation and/or mirror)
      -> destination canvas       (fit / fill / stretch / none)

The important ordering rules are:

* the clean aperture is interpreted in coded-pixel coordinates, including its
  x/y offset, and is cropped *before* anything else;
* non-square pixels are desqueezed *before* rotating, so a 90/270 degree turn
  never ends up scaling the wrong physical axis;
* orientation is a real affine map applied to the visible image, not merely a
  width/height swap, so mirrored/rotated subtitle-safe rectangles land on the
  correct side of the output.

All geometry uses exact rational arithmetic.  Values are snapped to half
pixels (round-half-to-even on ``v * 2``) only at serialisation time, which
preserves half-pixel offsets.

Usage:
    python3 geo.py INPUT.json OUTPUT.json
    python3 geo.py INPUT.json          # writes to stdout
"""

from __future__ import annotations

import json
import math
import re
import sys
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

Number = Fraction
Rect = Tuple[Fraction, Fraction, Fraction, Fraction]  # x, y, w, h


class GeometryError(ValueError):
    """Malformed or physically impossible frame description."""


# ---------------------------------------------------------------------------
# Exact number helpers
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def to_fraction(value: Any, what: str = "value") -> Fraction:
    """Convert a JSON number (or numeric string) to an exact rational."""
    if isinstance(value, bool):
        raise GeometryError(f"{what} must be a number, got a boolean")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GeometryError(f"{what} must be finite")
        # repr/str give the shortest decimal that round-trips; a fixture such
        # as 1.333 is therefore treated as exactly 1333/1000, not as the
        # nearest binary double.
        return Fraction(str(value))
    if isinstance(value, str):
        try:
            return Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise GeometryError(f"invalid {what}: {value!r}") from exc
    raise GeometryError(f"{what} must be a number, got {type(value).__name__}")


def as_pair(value: Any, what: str, default: Optional[Sequence[Any]] = None
            ) -> Tuple[Fraction, Fraction]:
    if value is None:
        if default is None:
            raise GeometryError(f"missing {what}")
        value = default
    if isinstance(value, dict):
        for keys in (("w", "h"), ("width", "height"), ("x", "y"),
                     ("num", "den"), ("numerator", "denominator")):
            if keys[0] in value and keys[1] in value:
                return (to_fraction(value[keys[0]], what),
                        to_fraction(value[keys[1]], what))
        if "value" in value:
            return as_pair(value["value"], what)
        raise GeometryError(f"{what} must contain two numbers")
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return as_pair(value[0], what)
        if len(value) != 2:
            raise GeometryError(f"{what} must be a pair")
        return to_fraction(value[0], what), to_fraction(value[1], what)
    # A bare number means a square size/ratio, e.g. "sar": 1.
    scalar = to_fraction(value, what)
    return scalar, scalar


def as_rect(value: Any, what: str = "rect") -> Rect:
    """Accept [x, y, w, h] or {x,y,w,h} / {left,top,width,height}."""
    if isinstance(value, dict):
        if "rect" in value and len(value) == 1:
            return as_rect(value["rect"], what)
        x = value.get("x", value.get("left", 0))
        y = value.get("y", value.get("top", 0))
        w = value.get("w", value.get("width"))
        h = value.get("h", value.get("height"))
        if w is None or h is None:
            raise GeometryError(f"{what} must provide width and height")
        return (to_fraction(x, what), to_fraction(y, what),
                to_fraction(w, what), to_fraction(h, what))
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(to_fraction(v, what) for v in value)  # type: ignore[return-value]
    raise GeometryError(f"{what} must be [x, y, w, h]")


def round_half_even(value: Fraction) -> int:
    """Round an exact rational to an integer, ties to even."""
    numerator, denominator = value.numerator, value.denominator
    quotient, remainder = divmod(numerator, denominator)  # floor division
    twice = 2 * remainder
    if twice > denominator or (twice == denominator and quotient % 2):
        quotient += 1
    return quotient


def snap_half(value: Number) -> Fraction:
    """Snap a coordinate to the nearest half pixel.

    This is the contract's ``round(v * 2) / 2``; the tie rule is half-to-even,
    matching Python's ``round`` while avoiding binary floating-point drift.
    """
    return Fraction(round_half_even(Fraction(value) * 2), 2)


def output_number(value: Number) -> float:
    """Serialise a coordinate, snapped to the half-pixel grid.

    The contract quantises coordinates with ``round(v * 2) / 2`` and prints
    three decimals.  Snapping here (rather than during the computation) keeps
    every intermediate value exact, so half-pixel offsets are preserved and do
    not accumulate error across the pipeline.
    """
    snapped = snap_half(value)
    return round(float(snapped), 3)


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


class Orientation:
    """A 2-D linear map ``x' = a*x + b*y, y' = c*x + d*y``.

    Screen coordinates are used (y grows downwards).  A clockwise quarter turn
    therefore maps the source point (x, y) to (-y, x).  The map is not anchored
    at an origin; callers normalise by the bounding box of the transformed
    visible image, which makes the result independent of whether the source
    rectangle starts at (0, 0) or not.
    """

    __slots__ = ("a", "b", "c", "d", "label")

    def __init__(self, a: Number, b: Number, c: Number, d: Number,
                 label: str = "custom") -> None:
        self.a = Fraction(a)
        self.b = Fraction(b)
        self.c = Fraction(c)
        self.d = Fraction(d)
        self.label = label

    # -- composition -------------------------------------------------------
    def then(self, other: "Orientation") -> "Orientation":
        """Return the map that applies *self* first, then *other*."""
        return Orientation(
            other.a * self.a + other.b * self.c,
            other.a * self.b + other.b * self.d,
            other.c * self.a + other.d * self.c,
            other.c * self.b + other.d * self.d,
            f"{self.label}>{other.label}",
        )

    # -- geometry ----------------------------------------------------------
    def apply_raw(self, x: Number, y: Number) -> Tuple[Fraction, Fraction]:
        return (self.a * x + self.b * y,
                self.c * x + self.d * y)

    def box(self, width: Number, height: Number
            ) -> Tuple[Fraction, Fraction, Fraction, Fraction]:
        """Bounding box of the transformed ``[0,width] x [0,height]`` rect."""
        points = [self.apply_raw(x, y)
                  for x, y in ((0, 0), (width, 0), (0, height), (width, height))]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)

    def dimensions(self, width: Number, height: Number
                   ) -> Tuple[Fraction, Fraction]:
        min_x, min_y, max_x, max_y = self.box(width, height)
        return max_x - min_x, max_y - min_y

    def inverse_apply(self, x: Number, y: Number) -> Tuple[Fraction, Fraction]:
        """Inverse of ``apply_raw`` (used by the round-trip tests)."""
        determinant = self.a * self.d - self.b * self.c
        if determinant == 0:
            raise GeometryError("orientation matrix is not invertible")
        return ((self.d * x - self.b * y) / determinant,
                (-self.c * x + self.a * y) / determinant)


# Canonical clockwise rotations.  r90 maps (x, y) -> (-y, x), which sends the
# source top-left corner to the transformed top-right corner.
IDENTITY = Orientation(1, 0, 0, 1, "identity")
R90 = Orientation(0, -1, 1, 0, "r90")
R180 = Orientation(-1, 0, 0, -1, "r180")
R270 = Orientation(0, 1, -1, 0, "r270")

# Mirroring primitives (horizontal flip mirrors left/right).
MIRROR_H = Orientation(-1, 0, 0, 1, "mirror")
MIRROR_V = Orientation(1, 0, 0, -1, "vflip")
TRANSPOSE = Orientation(0, 1, 1, 0, "transpose")
ANTI_TRANSPOSE = Orientation(0, -1, -1, 0, "antitranspose")


def rotation(angle: Number) -> Orientation:
    """Clockwise rotation by a multiple of 90 degrees."""
    angle = Fraction(angle) % 360
    if angle == 0:
        return IDENTITY
    if angle == 90:
        return R90
    if angle == 180:
        return R180
    if angle == 270:
        return R270
    raise GeometryError(
        f"unsupported rotation {angle} degrees; only multiples of 90 are defined")


def orientation_from_matrix(value: Any) -> Orientation:
    """Build an orientation from a 2x2 / 3x3 / 6-element affine matrix.

    Flattened values are row-major.  A 3x3 matrix is interpreted as

        [a b tx]
        [c d ty]
        [0 0  1]

    and translation is dropped, because the mapper anchors the visible image
    at the top-left of the transformed bounding box.  A 6-element list is read
    as ``[a, b, tx, c, d, ty]`` (row-major affine), the same convention.
    """
    if isinstance(value, dict):
        for key in ("matrix", "transform_matrix", "display_matrix", "affine",
                    "value", "rows", "linear"):
            if key in value:
                return orientation_from_matrix(value[key])
        if all(key in value for key in ("a", "b", "c", "d")):
            nums = [to_fraction(value[key], "matrix element")
                    for key in ("a", "b", "c", "d")]
            if "e" in value:
                nums.append(to_fraction(value["e"], "matrix element"))
            if "f" in value:
                nums.append(to_fraction(value["f"], "matrix element"))
            return orientation_from_matrix(nums)
        raise GeometryError("unsupported matrix description")

    if isinstance(value, str):
        return orientation_from_matrix(
            [to_fraction(item, "matrix element")
             for item in _NUMBER_RE.findall(value)])

    if not isinstance(value, (list, tuple)):
        raise GeometryError("orientation matrix must be a list")

    flat: List[Any] = []

    def flatten(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            for sub in item:
                flatten(sub)
        elif isinstance(item, dict):
            for key in ("x", "y", "w", "h"):
                if key not in item:
                    break
            else:
                return  # a point inside a matrix is not meaningful here
            raise GeometryError("unsupported matrix element")
        else:
            flat.append(item)

    for item in value:
        flatten(item)
    nums = [to_fraction(item, "matrix element") for item in flat]

    if len(nums) == 4:                       # 2x2 linear map
        a, b, c, d = nums
        return Orientation(a, b, c, d, "matrix")
    if len(nums) == 6:                       # affine, translation dropped
        a, b, _tx, c, d, _ty = nums
        return Orientation(a, b, c, d, "matrix")
    if len(nums) == 9:                       # 3x3, translation dropped
        a, b, _tx, c, d, _ty, g, h, i = nums
        if g == 0 and h == 0 and i != 0:
            if i != 1:
                a, b, c, d = a / i, b / i, c / i, d / i
            return Orientation(a, b, c, d, "matrix")
        raise GeometryError("matrix must be affine (last row 0 0 1)")
    raise GeometryError("orientation matrix must have 4, 6 or 9 elements")


_MIRROR_WORDS = {
    "mirror", "hflip", "flip", "fliph", "mirrorh", "horizontalflip",
    "fliphorizontal", "mirrorhorizontal",
}
_VFLIP_WORDS = {
    "vflip", "flipv", "mirrorv", "verticalflip", "flipvertical",
    "mirrorvertical",
}
_TRANSPOSE_WORDS = {"transpose", "transposed"}
_ANTITRANSPOSE_WORDS = {"antitranspose", "antitransposed"}
_IGNORED_WORDS = {"rotate", "rotation", "rot", "deg", "degree", "degrees"}
_IDENTITY_WORDS = {"", "none", "null", "identity", "r0", "0", "0deg"}


def orientation_from_string(text: str) -> Orientation:
    """Parse ``r90`` / ``mirror`` / ``r90+mirror`` / ``matrix(...)`` forms."""
    raw = text.strip().lower()
    if raw in _IDENTITY_WORDS:
        return IDENTITY

    if raw.startswith("matrix"):
        inner = raw[raw.find("(") + 1: raw.rfind(")")] if "(" in raw else raw[6:]
        return orientation_from_matrix(
            [to_fraction(item, "matrix element")
             for item in _NUMBER_RE.findall(inner)])

    # Normalise punctuation without destroying a leading minus sign.
    normalised = raw.replace("anti-transpose", "antitranspose")
    normalised = normalised.replace("counter-clockwise", "counterclockwise")
    normalised = normalised.replace("_", "")
    normalised = normalised.replace("(", " ").replace(")", " ")
    normalised = normalised.replace("[", " ").replace("]", " ")
    tokens = [token for token in re.split(r"[\s+,;|]+", normalised) if token]

    result = IDENTITY
    rotation_re = re.compile(
        r"(?:(cw|ccw|clockwise|counterclockwise))?"
        r"r?([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
        r"(?:(cw|ccw|clockwise|counterclockwise))?"
        r"(?:deg|degree|degrees)?"
    )

    for token in tokens:
        if token in _IGNORED_WORDS:
            continue
        if token in _IDENTITY_WORDS:
            continue
        if token in _MIRROR_WORDS:
            result = result.then(MIRROR_H)
            continue
        if token in _VFLIP_WORDS:
            result = result.then(MIRROR_V)
            continue
        if token in _TRANSPOSE_WORDS:
            result = result.then(TRANSPOSE)
            continue
        if token in _ANTITRANSPOSE_WORDS:
            result = result.then(ANTI_TRANSPOSE)
            continue

        match = rotation_re.fullmatch(token)
        if match:
            before, angle_text, after = match.groups()
            direction = before or after
            angle = to_fraction(angle_text, "rotation angle")
            if direction in ("ccw", "counterclockwise"):
                angle = -angle
            result = result.then(rotation(angle))
            continue

        raise GeometryError(f"unknown transform token {token!r} in {text!r}")

    return result


def orientation_from_spec(spec: Any) -> Orientation:
    if spec is None:
        return IDENTITY
    if isinstance(spec, str):
        return orientation_from_string(spec)
    if isinstance(spec, bool):
        return MIRROR_H if spec else IDENTITY
    if isinstance(spec, (int, float, Fraction)):
        return rotation(to_fraction(spec, "rotation angle"))
    if isinstance(spec, (list, tuple)):
        return orientation_from_matrix(spec)
    if isinstance(spec, dict):
        for key in ("matrix", "transform_matrix", "display_matrix", "affine"):
            if key in spec:
                result = orientation_from_matrix(spec[key])
                # An explicit mirror flag is applied after the matrix.
                if spec.get("mirror") or spec.get("hflip"):
                    result = result.then(MIRROR_H)
                if spec.get("vflip"):
                    result = result.then(MIRROR_V)
                return result
        angle = spec.get("rotate", spec.get("rotation", spec.get("angle")))
        result = IDENTITY if angle is None else rotation(to_fraction(angle, "rotation angle"))
        if spec.get("mirror") or spec.get("hflip") or spec.get("flip"):
            result = result.then(MIRROR_H)
        if spec.get("vflip") or spec.get("flip_v"):
            result = result.then(MIRROR_V)
        return result
    raise GeometryError(f"unsupported transform {spec!r}")


# ---------------------------------------------------------------------------
# Frame input helpers
# ---------------------------------------------------------------------------


def _first_present(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def frame_coded(frame: Dict[str, Any], aperture: Optional[Rect]
                ) -> Tuple[Fraction, Fraction]:
    """Coded (decoded) frame size in samples."""
    value = _first_present(frame, ("coded", "coded_size", "codedSize",
                                   "decoded", "size"))
    if value is not None:
        return as_pair(value, "coded size")
    if aperture is not None:
        # The contract always supplies a coded size; this fallback only keeps
        # older callers working while still bounding the aperture.
        return aperture[0] + aperture[2], aperture[1] + aperture[3]
    raise GeometryError("frame must provide a coded size or an aperture")


def frame_aperture(frame: Dict[str, Any], coded: Tuple[Fraction, Fraction]) -> Rect:
    value = _first_present(frame, ("aperture", "clean_aperture", "cleanAperture",
                                   "crop", "clap"))
    if value is None:
        return (Fraction(0), Fraction(0), coded[0], coded[1])

    if isinstance(value, dict) and "horizOff" in value and "vertOff" in value \
            and "x" not in value and "left" not in value:
        # ISO clean-aperture spelling: width/height plus offsets from centre.
        raw_width = value.get("width", value.get("w"))
        raw_height = value.get("height", value.get("h"))
        if raw_width is None or raw_height is None:
            raise GeometryError("aperture must provide width and height")
        width = to_fraction(raw_width, "aperture width")
        height = to_fraction(raw_height, "aperture height")
        if width < 0 or height < 0:
            raise GeometryError("aperture width and height must be non-negative")
        horiz = to_fraction(value["horizOff"], "horizontal aperture offset")
        vert = to_fraction(value["vertOff"], "vertical aperture offset")
        return ((coded[0] - width) / 2 + horiz,
                (coded[1] - height) / 2 + vert,
                width, height)

    aperture = as_rect(value, "aperture")
    if aperture[2] < 0 or aperture[3] < 0:
        raise GeometryError("aperture width and height must be non-negative")
    return aperture


def frame_sar(frame: Dict[str, Any]) -> Tuple[Fraction, Fraction]:
    value = _first_present(frame, ("sar", "sample_aspect_ratio",
                                   "sampleAspectRatio", "par",
                                   "pixel_aspect_ratio", "pixelAspectRatio"))
    sar = as_pair(value, "sample aspect ratio", default=(1, 1))
    if sar[0] <= 0 or sar[1] <= 0:
        raise GeometryError("sample aspect ratio must be positive")
    return sar


def frame_orientation(frame: Dict[str, Any]) -> Orientation:
    value = _first_present(frame, ("transform", "orientation", "rotation",
                                   "display_matrix", "transform_matrix",
                                   "matrix"))
    return orientation_from_spec(value)


def _canonical_strategy(value: Any, what: str = "canvas strategy") -> str:
    if isinstance(value, bool):
        return "fit" if value else "none"
    if not isinstance(value, str):
        raise GeometryError(f"{what} must be a string")
    key = value.strip().lower()
    key = re.sub(r"[\s_-]+", "", key)
    aliases = {
        "": "fit", "fit": "fit", "contain": "fit", "letterbox": "fit",
        "pillarbox": "fit", "preserve": "fit", "aspectfit": "fit",
        "fitcenter": "fit", "centered": "fit",
        "fill": "fill", "cover": "fill", "crop": "fill",
        "preservefill": "fill", "aspectfill": "fill",
        "stretch": "stretch", "exact": "stretch", "distort": "stretch",
        "nonuniform": "stretch", "nonuniformscale": "stretch",
        "none": "none", "original": "none", "actual": "none",
        "1:1": "none", "identity": "none",
        "fitwidth": "fitwidth", "width": "fitwidth",
        "fitheight": "fitheight", "height": "fitheight",
    }
    if key not in aliases:
        raise GeometryError(f"unknown {what}: {value!r}")
    return aliases[key]


def frame_strategy(frame: Dict[str, Any], default: Any) -> str:
    value = _first_present(frame, ("strategy", "fit", "canvas_strategy",
                                   "canvas_fit", "scale_mode", "mode"))
    return _canonical_strategy(default if value is None else value)


def frame_safe(frame: Dict[str, Any]) -> Optional[Tuple[Rect, bool]]:
    """Return an optional subtitle-safe rect and whether it is fractional.

    Accepted inputs (all optional; the default safe rect is the visible image):

    * ``[x, y, w, h]`` in *coded* pixels, including the aperture offset;
    * ``[x, y, w, h]`` with all components in [0, 1], treated as fractions of
      the visible image (a heuristic that an explicit flag can override);
    * a dict with the same fields plus ``relative`` / ``normalized`` /
      ``fraction`` / ``unit: "fraction"``.
    """
    value = _first_present(frame, ("safe", "safe_area", "safe_rect",
                                   "subtitle_safe", "subtitleSafe",
                                   "subtitle_safe_area", "action_safe"))
    if value is None:
        return None

    relative: Optional[bool] = None
    if isinstance(value, dict):
        for key in ("relative", "normalized", "normalised", "fraction",
                    "fractional"):
            if key in value:
                relative = bool(value[key])
        unit = value.get("unit")
        if isinstance(unit, str):
            relative = unit.strip().lower() in ("fraction", "fractional",
                                                "normalized", "normalised",
                                                "relative", "norm")
        if "rect" in value:
            value = value["rect"]

    rect = as_rect(value, "safe area")
    if relative is None:
        # A bare [x, y, w, h] whose components all fit in [0, 1] is read as a
        # fraction of the visible image.  An explicit flag (or unit) overrides
        # this heuristic, so a genuinely tiny pixel rect remains unambiguous.
        relative = all(Fraction(0) <= component <= Fraction(1)
                       for component in rect)
    return rect, relative


# ---------------------------------------------------------------------------
# Frame mapping
# ---------------------------------------------------------------------------


def _scale_for(strategy: str, display_width: Fraction, display_height: Fraction,
               canvas_width: Fraction, canvas_height: Fraction
               ) -> Tuple[Fraction, Fraction]:
    if display_width <= 0 or display_height <= 0:
        raise GeometryError("visible image has non-positive size")
    if strategy == "fit":
        scale = min(canvas_width / display_width,
                    canvas_height / display_height)
        return scale, scale
    if strategy == "fill":
        scale = max(canvas_width / display_width,
                    canvas_height / display_height)
        return scale, scale
    if strategy == "stretch":
        return (canvas_width / display_width,
                canvas_height / display_height)
    if strategy == "fitwidth":
        scale = canvas_width / display_width
        return scale, scale
    if strategy == "fitheight":
        scale = canvas_height / display_height
        return scale, scale
    if strategy == "none":
        return Fraction(1), Fraction(1)
    raise AssertionError(strategy)


def _bbox(points: Sequence[Tuple[Fraction, Fraction]]) -> Rect:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return left, top, right - left, bottom - top


def intersect_rect(rect: Rect, bounds: Rect) -> Optional[Rect]:
    """Intersection of two rectangles, or ``None`` when it is empty."""
    left = max(rect[0], bounds[0])
    top = max(rect[1], bounds[1])
    right = min(rect[0] + rect[2], bounds[0] + bounds[2])
    bottom = min(rect[1] + rect[3], bounds[1] + bounds[3])
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


def compute_frame(frame: Dict[str, Any], canvas: Any, default_strategy: Any
                  ) -> Dict[str, Any]:
    if not isinstance(frame, dict):
        raise GeometryError("each frame must be an object")
    if frame.get("id") is None:
        raise GeometryError("each frame must have an id")
    frame_id = frame["id"]

    aperture_hint = None
    raw_aperture = _first_present(frame, ("aperture", "clean_aperture",
                                          "cleanAperture", "crop", "clap"))
    if raw_aperture is not None and not isinstance(raw_aperture, dict):
        try:
            aperture_hint = as_rect(raw_aperture, "aperture")
        except GeometryError:
            aperture_hint = None

    coded = frame_coded(frame, aperture_hint)
    if coded[0] < 0 or coded[1] < 0:
        raise GeometryError("coded size must be non-negative")

    aperture = frame_aperture(frame, coded)
    aperture_width, aperture_height = aperture[2], aperture[3]

    canvas_width, canvas_height = as_pair(canvas, "canvas")
    if canvas_width < 0 or canvas_height < 0:
        raise GeometryError("canvas dimensions must be non-negative")

    strategy = frame_strategy(frame, default_strategy)

    # The coded frame bounds the decoded samples: an aperture that extends
    # outside it is out of frame, and only the intersection is visible.  This
    # clipping happens in coded-sample coordinates, before any aspect or
    # orientation conversion.
    coded_bounds = (Fraction(0), Fraction(0), coded[0], coded[1])
    visible = (intersect_rect(aperture, coded_bounds)
               if aperture_width > 0 and aperture_height > 0 else None)

    # Degenerate clean aperture (or one entirely outside the coded frame):
    # there is no visible sample.  Keep the output schema valid instead of
    # dividing by zero.
    if visible is None:
        zero_offset = (canvas_width / 2, canvas_height / 2)
        empty = (zero_offset[0], zero_offset[1], Fraction(0), Fraction(0))
        return _format_frame(frame_id, (0, 0), (0, 0), zero_offset, empty,
                             empty)

    sar = frame_sar(frame)
    sar_x = sar[0] / sar[1]
    orientation = frame_orientation(frame)

    # 1. The visible image is the clipped clean aperture, in coded samples.
    # 2. Desqueeze the horizontal axis to obtain square-pixel display units.
    display_width_0 = visible[2] * sar_x
    display_height_0 = visible[3]

    # 3. Apply orientation.  The transformed visible image is anchored at the
    #    top-left of its bounding box, so its dimensions are the bbox size.
    min_x, min_y, max_x, max_y = orientation.box(display_width_0,
                                                 display_height_0)
    display_width = max_x - min_x
    display_height = max_y - min_y

    # 4. Fit the transformed visible image into the destination canvas.
    scale_x, scale_y = _scale_for(strategy, display_width, display_height,
                                  canvas_width, canvas_height)
    output_width = display_width * scale_x
    output_height = display_height * scale_y
    offset_x = (canvas_width - output_width) / 2
    offset_y = (canvas_height - output_height) / 2

    def to_canvas(x: Fraction, y: Fraction) -> Tuple[Fraction, Fraction]:
        """Map square-pixel display coordinates to canvas coordinates."""
        raw_x, raw_y = orientation.apply_raw(x, y)
        return (offset_x + (raw_x - min_x) * scale_x,
                offset_y + (raw_y - min_y) * scale_y)

    # Content boundary: the transformed visible image inside the canvas.
    content = _bbox([to_canvas(0, 0),
                     to_canvas(display_width_0, 0),
                     to_canvas(0, display_height_0),
                     to_canvas(display_width_0, display_height_0)])

    # Subtitle-safe area: an optional pre-transform rectangle in visible-image
    # (display) coordinates, transformed with exactly the same matrix.
    safe_input = frame_safe(frame)
    if safe_input is None:
        safe_rect = content
    else:
        safe_raw, relative = safe_input
        if relative:
            left = safe_raw[0] * display_width_0
            right = (safe_raw[0] + safe_raw[2]) * display_width_0
            top = safe_raw[1] * display_height_0
            bottom = (safe_raw[1] + safe_raw[3]) * display_height_0
        else:
            # Absolute coordinates are coded samples: remove the visible
            # aperture origin, then desqueeze the horizontal axis.
            left = (safe_raw[0] - visible[0]) * sar_x
            right = (safe_raw[0] + safe_raw[2] - visible[0]) * sar_x
            top = safe_raw[1] - visible[1]
            bottom = safe_raw[1] + safe_raw[3] - visible[1]
        safe_rect = _bbox([to_canvas(left, top),
                           to_canvas(right, top),
                           to_canvas(left, bottom),
                           to_canvas(right, bottom)])

    return _format_frame(frame_id, (display_width, display_height),
                         (output_width, output_height),
                         (offset_x, offset_y), content, safe_rect)


def _format_frame(frame_id: Any,
                  display_size: Tuple[Fraction, Fraction],
                  output_size: Tuple[Fraction, Fraction],
                  offset: Tuple[Fraction, Fraction],
                  content: Rect,
                  safe: Rect) -> Dict[str, Any]:
    """Serialise one frame with the schema and quantisation from README.md."""
    def pair(values: Tuple[Fraction, Fraction]) -> List[float]:
        return [output_number(values[0]), output_number(values[1])]

    return {
        "id": frame_id,
        "disp": pair(display_size),
        "out": pair(output_size),
        "offset": pair(offset),
        "corners": [pair((content[0], content[1])),
                    pair((content[0] + content[2],
                          content[1] + content[3]))],
        "safe": [output_number(safe[0]), output_number(safe[1]),
                 output_number(safe[2]), output_number(safe[3])],
    }


def map_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise GeometryError("top-level input must be an object")
    frames = payload.get("frames")
    if not isinstance(frames, list):
        raise GeometryError("input must contain a frames array")
    canvas = payload.get("canvas", payload.get("target"))
    if canvas is None:
        raise GeometryError("input must contain a canvas")
    default_strategy = payload.get("strategy", payload.get("fit", "fit"))

    return {"frames": [compute_frame(frame, canvas, default_strategy)
                       for frame in frames]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) not in (1, 2):
        print("usage: geo.py INPUT.json [OUTPUT.json]", file=sys.stderr)
        return 2
    input_path = arguments[0]
    output_path = arguments[1] if len(arguments) == 2 else None

    try:
        if input_path == "-":
            payload = json.load(sys.stdin)
        else:
            with open(input_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        result = map_payload(payload)
        text = json.dumps(result, indent=1, ensure_ascii=False) + "\n"
        if output_path is None or output_path == "-":
            sys.stdout.write(text)
        else:
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(text)
    except (OSError, json.JSONDecodeError, GeometryError) as exc:
        print(f"geo.py: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

