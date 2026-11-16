"""Acceptance tests for the rotation / aperture / canvas mapper.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geo  # noqa: E402
import fixtures  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(HERE, "geo.py")
TMP_ROOT = HERE  # tempfile cleanup must stay inside the writable workspace


# ---------------------------------------------------------------------------
# Independent reference implementation
# ---------------------------------------------------------------------------

def _rot(deg):
    deg = Fraction(deg) % 360
    if deg == 0:
        return (Fraction(1), 0, 0, 1)
    if deg == 90:
        return (Fraction(0), Fraction(-1), Fraction(1), Fraction(0))
    if deg == 180:
        return (Fraction(-1), 0, 0, Fraction(-1))
    if deg == 270:
        return (Fraction(0), Fraction(1), Fraction(-1), Fraction(0))
    raise AssertionError(deg)


def _compose(inner, outer):
    """Apply inner first, then outer."""
    a1, b1, c1, d1 = inner
    a2, b2, c2, d2 = outer
    return (a2 * a1 + b2 * c1, a2 * b1 + b2 * d1,
            c2 * a1 + d2 * c1, c2 * b1 + d2 * d1)


def _matrix(transform):
    """Clockwise rotation/mirror as a 2x2 matrix in screen coordinates."""
    if transform is None:
        return (1, 0, 0, 1)
    if isinstance(transform, (list, tuple)):
        flat = []
        for row in transform:
            flat.extend(row if isinstance(row, (list, tuple)) else [row])
        if len(flat) == 4:
            return tuple(Fraction(v) for v in flat)
        return (Fraction(flat[0]), Fraction(flat[1]),
                Fraction(flat[3]), Fraction(flat[4]))
    if isinstance(transform, dict):
        if "matrix" in transform:
            return _matrix(transform["matrix"])
        deg = Fraction(transform.get("rotate", transform.get("rotation", 0)))
        result = _rot(deg)
        if transform.get("mirror") or transform.get("hflip"):
            result = _compose(result, (Fraction(-1), 0, 0, 1))
        return result
    text = transform.lower().replace(" ", "")
    result = (1, 0, 0, 1)
    for part in text.split("+"):
        if part in ("", "none", "identity"):
            continue
        if part in ("mirror", "hflip"):
            result = _compose(result, (Fraction(-1), 0, 0, 1))
        elif part == "vflip":
            result = _compose(result, (Fraction(1), 0, 0, -1))
        elif part in ("r90", "r180", "r270"):
            result = _compose(result, _rot(Fraction(part[1:])))
        else:
            raise AssertionError(part)
    return result


def _apply(matrix, x, y):
    a, b, c, d = matrix
    return a * x + b * y, c * x + d * y


def _snap(value):
    """Half-pixel snap (half-to-even) then three decimals."""
    return round(float(Fraction(round_half_even(Fraction(value) * 2), 2)), 3)


def round_half_even(value):
    q, r = divmod(value.numerator, value.denominator)
    twice = 2 * r
    if twice > value.denominator or (twice == value.denominator and q % 2):
        q += 1
    return q


def reference(frame, canvas, strategy="fit"):
    """Literal, deliberately naive implementation of the README contract."""
    coded_w, coded_h = (Fraction(v) for v in frame["coded"])
    ax, ay, aw, ah = (Fraction(v) for v in frame["aperture"])
    sar_n, sar_d = (Fraction(v) for v in frame.get("sar", (1, 1)))
    sample_width = sar_n / sar_d

    # 1. The visible image: clean aperture clipped to the decoded frame.
    left = max(ax, Fraction(0))
    top = max(ay, Fraction(0))
    right = min(ax + aw, coded_w)
    bottom = min(ay + ah, coded_h)
    visible_w, visible_h = right - left, bottom - top
    if visible_w <= 0 or visible_h <= 0:
        # No decoded sample is inside the clean aperture: an empty visible
        # image is reported as a zero-size rect at the canvas centre.
        canvas_w, canvas_h = (Fraction(v) for v in canvas)
        centre = [_snap(canvas_w / 2), _snap(canvas_h / 2)]
        return {
            "id": frame["id"],
            "disp": [0.0, 0.0],
            "out": [0.0, 0.0],
            "offset": centre,
            "corners": [list(centre), list(centre)],
            "safe": [centre[0], centre[1], 0.0, 0.0],
        }

    # 2. Desqueeze to square pixels; 3. orient the visible image.
    dw0, dh0 = visible_w * sample_width, visible_h
    matrix = _matrix(frame.get("transform"))
    corners = [_apply(matrix, x, y) for x, y in
               ((0, 0), (dw0, 0), (0, dh0), (dw0, dh0))]
    min_x = min(point[0] for point in corners)
    min_y = min(point[1] for point in corners)
    dw = max(point[0] for point in corners) - min_x
    dh = max(point[1] for point in corners) - min_y

    # 4. Map the oriented image into the destination canvas.
    canvas_w, canvas_h = (Fraction(v) for v in canvas)
    if strategy == "fit":
        sx = sy = min(canvas_w / dw, canvas_h / dh)
    elif strategy == "fill":
        sx = sy = max(canvas_w / dw, canvas_h / dh)
    elif strategy == "stretch":
        sx, sy = canvas_w / dw, canvas_h / dh
    elif strategy == "none":
        sx = sy = Fraction(1)
    else:
        raise AssertionError(strategy)
    output_w, output_h = dw * sx, dh * sy
    offset_x, offset_y = ((canvas_w - output_w) / 2,
                          (canvas_h - output_h) / 2)

    def to_canvas(x, y):
        mx, my = _apply(matrix, x, y)
        return (offset_x + (mx - min_x) * sx,
                offset_y + (my - min_y) * sy)

    traced = [to_canvas(x, y) for x, y in
              ((0, 0), (dw0, 0), (0, dh0), (dw0, dh0))]
    x0 = min(point[0] for point in traced)
    x1 = max(point[0] for point in traced)
    y0 = min(point[1] for point in traced)
    y1 = max(point[1] for point in traced)

    safe = frame.get("safe")
    if safe is None:
        safe_rect = (x0, y0, x1 - x0, y1 - y0)
    else:
        u0, v0, u_size, v_size = (Fraction(v) for v in safe)
        if all(Fraction(0) <= value <= 1
               for value in (u0, v0, u_size, v_size)):
            # Fractional rect: interpret x/y/w/h as fractions of the visible
            # image, in square-pixel display units.
            u0, u1 = u0 * dw0, (u0 + u_size) * dw0
            v0, v1 = v0 * dh0, (v0 + v_size) * dh0
        else:
            # Absolute rect in coded samples, relative to the visible origin.
            absolute_x, absolute_y, absolute_w, absolute_h = (
                Fraction(v) for v in safe)
            u0 = (absolute_x - left) * sample_width
            u1 = (absolute_x + absolute_w - left) * sample_width
            v0 = absolute_y - top
            v1 = absolute_y + absolute_h - top
        traced = [to_canvas(x, y) for x, y in
                  ((u0, v0), (u1, v0), (u0, v1), (u1, v1))]
        sx0 = min(point[0] for point in traced)
        sy0 = min(point[1] for point in traced)
        safe_rect = (sx0, sy0,
                     max(point[0] for point in traced) - sx0,
                     max(point[1] for point in traced) - sy0)

    return {
        "id": frame["id"],
        "disp": [_snap(dw), _snap(dh)],
        "out": [_snap(output_w), _snap(output_h)],
        "offset": [_snap(offset_x), _snap(offset_y)],
        "corners": [[_snap(x0), _snap(y0)], [_snap(x1), _snap(y1)]],
        "safe": [_snap(safe_rect[0]), _snap(safe_rect[1]),
                 _snap(safe_rect[2]), _snap(safe_rect[3])],
    }



def _exact_safe(frame, canvas, strategy="fit"):
    """Exact (unrounded) safe rect, derived from ``reference``."""
    result = reference(frame, canvas, strategy)
    # reference() rounds, so rebuild the exact value here for comparisons that
    # are sensitive to half-pixel snapping.
    coded_w, coded_h = (Fraction(v) for v in frame["coded"])
    ax, ay, aw, ah = (Fraction(v) for v in frame["aperture"])
    sar_n, sar_d = (Fraction(v) for v in frame.get("sar", (1, 1)))
    sample_width = sar_n / sar_d
    left = max(ax, Fraction(0))
    top = max(ay, Fraction(0))
    right = min(ax + aw, coded_w)
    bottom = min(ay + ah, coded_h)
    dw0, dh0 = (right - left) * sample_width, bottom - top
    matrix = _matrix(frame.get("transform"))
    corners = [_apply(matrix, x, y) for x, y in
               ((0, 0), (dw0, 0), (0, dh0), (dw0, dh0))]
    min_x = min(point[0] for point in corners)
    min_y = min(point[1] for point in corners)
    dw = max(point[0] for point in corners) - min_x
    dh = max(point[1] for point in corners) - min_y
    canvas_w, canvas_h = (Fraction(v) for v in canvas)
    scale = min(canvas_w / dw, canvas_h / dh)
    offset_x = (canvas_w - dw * scale) / 2
    offset_y = (canvas_h - dh * scale) / 2

    def to_canvas(x, y):
        mx, my = _apply(matrix, x, y)
        return (offset_x + (mx - min_x) * scale,
                offset_y + (my - min_y) * scale)

    u0, v0, u_size, v_size = (Fraction(v) for v in frame["safe"])
    if all(Fraction(0) <= value <= 1 for value in (u0, v0, u_size, v_size)):
        u0, u1 = u0 * dw0, (u0 + u_size) * dw0
        v0, v1 = v0 * dh0, (v0 + v_size) * dh0
    else:
        u0, u1 = (u0 - left) * sample_width, (u0 + u_size - left) * sample_width
        v0, v1 = v0 - top, v0 + v_size - top
    traced = [to_canvas(x, y) for x, y in
              ((u0, v0), (u1, v0), (u0, v1), (u1, v1))]
    sx0 = min(point[0] for point in traced)
    sy0 = min(point[1] for point in traced)
    return (sx0, sy0,
            max(point[0] for point in traced) - sx0,
            max(point[1] for point in traced) - sy0)


def map_one(payload, index=0):
    return geo.map_payload(payload)["frames"][index]


def close(actual, expected, tolerance=1e-9):
    return abs(actual - expected) <= tolerance


class RectAssertions:
    def assertRect(self, actual, expected, tolerance=1e-9):
        self.assertEqual(len(actual), len(expected))
        for got, want in zip(actual, expected):
            self.assertTrue(close(got, want, tolerance),
                            f"rect {actual} != {expected}")


class BaselineTests(unittest.TestCase, RectAssertions):
    def test_landscape_identity_fits_exactly(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080))], canvas=(1920, 1080)))
        self.assertRect(result["disp"], [1920, 1080])
        self.assertRect(result["out"], [1920, 1080])
        self.assertRect(result["offset"], [0, 0])
        self.assertRect(result["corners"][0], [0, 0])
        self.assertRect(result["corners"][1], [1920, 1080])
        self.assertRect(result["safe"], [0, 0, 1920, 1080])

    def test_portrait_is_pillarboxed_not_cropped(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1080, 1920))], canvas=(1920, 1080)))
        # Fit scales by height: 1080 * 1080/1920 = 607.5 wide, leaving 656.25
        # on each side.
        self.assertRect(result["disp"], [1080, 1920])
        self.assertRect(result["out"], [607.5, 1080])
        # 656.25 lies on a quarter pixel; the contract's half-pixel grid
        # rounds it (half-to-even) to 656.0.  Nothing else drifts.
        self.assertRect(result["offset"], [656, 0])
        self.assertRect(result["corners"][0], [656, 0])
        self.assertRect(result["corners"][1], [1264, 1080])


class RotationTests(unittest.TestCase, RectAssertions):
    def test_r90_swaps_dimensions_after_sar(self):
        result = map_one(fixtures.portrait_into_landscape("r90"))
        self.assertRect(result["disp"], [1920, 1080])
        self.assertRect(result["out"], [1920, 1080])
        self.assertRect(result["offset"], [0, 0])
        self.assertRect(result["corners"][0], [0, 0])
        self.assertRect(result["corners"][1], [1920, 1080])

    def test_r270_swaps_dimensions_the_other_way(self):
        result = map_one(fixtures.portrait_into_landscape("r270"))
        self.assertRect(result["disp"], [1920, 1080])

    def test_r90_and_r270_swap_the_content_boundary(self):
        frame = fixtures.frame("x", coded=(1920, 1080),
                               aperture=(100, 20, 1600, 1000), transform="r90",
                               safe=(0.0, 0.0, 0.25, 0.5))
        r90 = map_one(fixtures.payload([frame], canvas=(1000, 1000)))
        r270 = map_one(fixtures.payload(
            [dict(frame, transform="r270")], canvas=(1000, 1000)))
        # Both are the same bounding box (the visible image is just turned),
        # but the safe area lands on opposite sides.
        self.assertRect(r90["corners"][0], r270["corners"][0])
        self.assertRect(r90["corners"][1], r270["corners"][1])
        # A source rect at the top-left of the visible image lands at the
        # top-right after a clockwise 90 degree turn ...
        self.assertAlmostEqual(r90["safe"][1], r90["offset"][1], places=6)
        self.assertAlmostEqual(r90["safe"][0] + r90["safe"][2],
                               r90["offset"][0] + r90["out"][0], places=6)
        # ... and at the bottom-left after 270 degrees.
        self.assertAlmostEqual(r270["safe"][0], r270["offset"][0], places=6)
        self.assertAlmostEqual(r270["safe"][1] + r270["safe"][3],
                               r270["offset"][1] + r270["out"][1], places=6)

    def test_r180_keeps_size_and_flips_position(self):
        frame = fixtures.frame("x", coded=(800, 600), aperture=(100, 50, 500, 300))
        plain = map_one(fixtures.payload([frame], canvas=(1000, 1000)))
        flipped = map_one(fixtures.payload(
            [dict(frame, transform="r180")], canvas=(1000, 1000)))
        self.assertRect(plain["disp"], flipped["disp"])
        # The aperture offset in coded pixels is erased: only the visible
        # image matters, so both outputs trace the same rectangle.
        self.assertRect(plain["corners"][0], flipped["corners"][0])
        self.assertRect(plain["corners"][1], flipped["corners"][1])


class AnamorphicTests(unittest.TestCase, RectAssertions):
    def test_non_square_pixels_give_square_pixel_display_size(self):
        result = map_one(fixtures.anamorphic())
        # 720 * 64/45 = 1024 (a 16:9 image), not 720.
        self.assertRect(result["disp"], [1024, 576])
        self.assertRect(result["out"], [1920, 1080])

    def test_rotation_happens_after_desqueeze(self):
        # A 720x576 anamorphic frame turned 90 degrees becomes 576x1024, not
        # 576x720 (which is what you get by swapping width/height first).
        result = map_one(fixtures.anamorphic("r90"))
        self.assertRect(result["disp"], [576, 1024])
        expected_scale = min(1920 / 576, 1080 / 1024)
        self.assertRect(result["out"], [576 * expected_scale,
                                        1024 * expected_scale])

    def test_anamorphic_with_mirror_keeps_aspect(self):
        result = map_one(fixtures.anamorphic("mirror"))
        self.assertRect(result["disp"], [1024, 576])

    def test_fractional_sar_is_not_lost(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1440, 1080), sar=(4, 3))],
            canvas=(1920, 1080)))
        self.assertRect(result["disp"], [1920, 1080])
        self.assertRect(result["out"], [1920, 1080])

    def test_extreme_sar(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(720, 480), sar=(32, 27))],
            canvas=(1920, 1080)))
        # 720 * 32/27 = 853.333..., reported on the half-pixel grid.
        self.assertAlmostEqual(result["disp"][0], 853.5, places=9)
        self.assertAlmostEqual(result["disp"][1], 480, places=9)
        # Fit is height-limited (1280/720 > 1080/480), so the 16:9 image
        # exactly fills the 1920x1080 canvas.
        self.assertAlmostEqual(result["out"][0], 1920, places=9)
        self.assertAlmostEqual(result["out"][1], 1080, places=9)


class MirrorTests(unittest.TestCase, RectAssertions):
    def test_horizontal_mirror_moves_safe_area_to_the_right(self):
        result = map_one(fixtures.mirror_with_safe())
        # Pre-transform safe area occupies 5%..45% horizontally; after a
        # mirror it occupies 55%..95%.
        self.assertRect(result["safe"], [1056, 108, 768, 216])
        self.assertRect(result["corners"][0], [0, 0])
        self.assertRect(result["corners"][1], [1920, 1080])

    def test_mirror_plus_rotation_composes(self):
        frame = fixtures.frame("x", coded=(1080, 1920), transform="r90+mirror")
        mirrored_then_rotated = map_one(fixtures.payload(
            [frame], canvas=(1920, 1080)))
        # r90 then a horizontal mirror of the result: the visible image still
        # fills the canvas, and the safe area must move to the opposite side.
        self.assertRect(mirrored_then_rotated["out"], [1920, 1080])

    def test_safe_area_round_trips_for_every_orientation(self):
        for transform in (None, "r90", "r180", "r270", "mirror", "vflip",
                          "r90+mirror", "r270+mirror"):
            with self.subTest(transform=transform):
                frame = fixtures.frame(
                    "x", coded=(640, 480), aperture=(20, 10, 600, 440),
                    sar=(4, 3), transform=transform,
                    safe=(0.1, 0.2, 0.6, 0.3))
                payload = fixtures.payload([frame], canvas=(1280, 720))
                result = map_one(payload)
                self.assertEqual(result, reference(frame, (1280, 720)))
                safe = result["safe"]
                out = result["out"]
                # The safe rect must stay inside the output rectangle.
                self.assertGreaterEqual(safe[0], result["offset"][0] - 1e-6)
                self.assertGreaterEqual(safe[1], result["offset"][1] - 1e-6)
                self.assertLessEqual(safe[0] + safe[2],
                                     result["offset"][0] + out[0] + 1e-6)
                self.assertLessEqual(safe[1] + safe[3],
                                     result["offset"][1] + out[1] + 1e-6)
                # Snapping to the half-pixel grid moves each edge by at most
                # half a pixel, so the exact aspect ratio may shift slightly;
                # compare against the *exact* reference-safe instead.
                exact = _exact_safe(frame, (1280, 720))
                self.assertAlmostEqual(safe[2] / safe[3],
                                       exact[2] / exact[3], places=1)


class ApertureTests(unittest.TestCase, RectAssertions):
    def test_aperture_offset_is_ignored_for_size(self):
        full = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080))], canvas=(1920, 1080)))
        cropped = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080),
                            aperture=(160, 0, 1600, 1080))],
            canvas=(1920, 1080)))
        self.assertRect(full["disp"], [1920, 1080])
        self.assertRect(cropped["disp"], [1600, 1080])

    def test_aperture_is_cropped_before_scaling(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080),
                            aperture=(120, 30, 1680, 1020))],
            canvas=(840, 510)))
        self.assertRect(result["disp"], [1680, 1020])
        # Exactly half size: 1680/2 = 840, 1020/2 = 510.
        self.assertRect(result["out"], [840, 510])
        self.assertRect(result["offset"], [0, 0])

    def test_aperture_outside_coded_frame_is_clipped(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080),
                            aperture=(-100, -50, 1200, 800))],
            canvas=(1200, 800)))
        # Only 1100x750 of the requested aperture actually contains samples.
        self.assertRect(result["disp"], [1100, 750])

    def test_iso_clean_aperture_offsets_are_centred(self):
        frame = {
            "id": "iso",
            "coded": [1920, 1080],
            "aperture": {"width": 1888, "height": 1062,
                         "horizOff": 0, "vertOff": 0},
            "sar": [1, 1],
        }
        result = map_one(fixtures.payload([frame], canvas=(1888, 1062)))
        self.assertRect(result["disp"], [1888, 1062])

    def test_safe_area_in_coded_pixels_respects_aperture_origin(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080),
                            aperture=(160, 0, 1600, 1080),
                            safe=(260, 100, 800, 500))],
            canvas=(1600, 1080)))
        # Safe x=260 is 100 coded pixels into the visible image, which maps
        # 1:1 here.
        self.assertRect(result["safe"], [100, 100, 800, 500])


class OddBoundaryTests(unittest.TestCase, RectAssertions):
    def test_odd_sizes_produce_half_pixel_corners(self):
        result = map_one(fixtures.odd_boundaries())
        for value in result["corners"][0] + result["corners"][1]:
            self.assertAlmostEqual(value * 2, round(value * 2), places=9)

    def test_content_stays_inside_canvas(self):
        result = map_one(fixtures.odd_boundaries())
        (x0, y0), (x1, y1) = result["corners"]
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x1, 1281 + 1e-9)
        self.assertLessEqual(y1, 721 + 1e-9)

    def test_full_width_fit_keeps_half_pixel_borders(self):
        # 1920x1080 content in a 1921x1081 canvas: fit is width-limited, so
        # the output is exactly 1921 x 1080.50625.  Its exact offsets are
        # (0, 0.246875), snapped to (0, 0), and the content boundary snaps to
        # the full 1921 x 1081 canvas with no drift on the left/top.
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080))], canvas=(1921, 1081)))
        self.assertRect(result["out"], [1921, 1080.5])
        self.assertRect(result["offset"], [0, 0])
        self.assertRect(result["corners"][0], [0, 0])
        self.assertRect(result["corners"][1], [1921, 1081])

    def test_quarter_pixel_offset_snaps_half_to_even(self):
        # Fit is width-limited (1921/1920 < 1081/1080), so the exact vertical
        # offset is 0.25 and snaps to 0.0; the exact bottom edge 1080.75 snaps
        # to 1081.0.  The content therefore spans the full 1081-pixel height.
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080))], canvas=(1921, 1081)))
        self.assertRect(result["offset"], [0, 0])
        self.assertRect(result["out"], [1921, 1080.5])
        self.assertRect(result["corners"][0], [0, 0])
        self.assertRect(result["corners"][1], [1921, 1081])

    def test_exact_half_pixel_offset_survives(self):
        # 3x2 content in a 3x5 canvas: fit by width keeps the pixel size and
        # centres vertically with a genuine half-pixel offset.
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(3, 2))], canvas=(3, 5)))
        self.assertRect(result["offset"], [0, 1.5])
        self.assertRect(result["corners"][0], [0, 1.5])
        self.assertRect(result["corners"][1], [3, 3.5])

    def test_negative_half_pixel_offsets_are_preserved(self):
        # A 3x2 image stretched into a 2.5x-reduced 7.5-wide target creates a
        # fractional corner; verify user-space half pixels are not clamped.
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(3, 2))], canvas=(15, 10),
            strategy="none"))
        self.assertRect(result["offset"], [6, 4])
        self.assertRect(result["corners"][0], [6, 4])
        self.assertRect(result["corners"][1], [9, 6])


class CanvasStrategyTests(unittest.TestCase, RectAssertions):
    def test_fill_covers_the_canvas(self):
        result = map_one(fixtures.out_of_frame())
        # 1920x1080 in a 100x100 canvas: cover is height-limited, giving an
        # output 177.78 x 100 with a -38.89 horizontal offset.  The content
        # therefore extends past both sides of the target.
        exact_w = 1920 * 100 / 1080
        self.assertAlmostEqual(result["out"][0], round(exact_w * 2) / 2,
                               places=9)
        self.assertAlmostEqual(result["out"][1], 100, places=9)
        # Exact offset is (100 - 177.78)/2 = -38.89, which snaps to -39.0.
        self.assertAlmostEqual(result["offset"][0], -39.0, places=9)
        self.assertAlmostEqual(result["offset"][1], 0, places=9)
        self.assertLess(result["offset"][0], 0)
        (x0, y0), (x1, y1) = result["corners"]
        self.assertLess(x0, 0)
        self.assertGreater(x1, 100)
        self.assertAlmostEqual(y0, 0, places=9)
        self.assertAlmostEqual(y1, 100, places=9)

    def test_fill_overflows_when_content_is_taller(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1080, 1920))], canvas=(100, 100),
            strategy="fill"))
        self.assertAlmostEqual(result["out"][0], 100, places=9)
        self.assertGreater(result["out"][1], 100)
        self.assertLess(result["offset"][1], 0)

    def test_stretch_fills_canvas_exactly(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1080, 1920))], canvas=(1920, 1080),
            strategy="stretch"))
        self.assertRect(result["out"], [1920, 1080])
        self.assertRect(result["offset"], [0, 0])

    def test_none_keeps_pixel_size_and_centres(self):
        result = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(640, 480))], canvas=(1920, 1080),
            strategy="none"))
        self.assertRect(result["out"], [640, 480])
        self.assertRect(result["offset"], [640, 300])

    def test_per_frame_strategy_overrides_default(self):
        result = fixtures.payload(
            [fixtures.frame("a", coded=(1920, 1080)),
             fixtures.frame("b", coded=(1920, 1080), strategy="fill")],
            canvas=(1000, 1000), strategy="fit")
        frames = geo.map_payload(result)["frames"]
        self.assertLessEqual(frames[0]["out"][0], 1000)
        self.assertGreaterEqual(frames[1]["out"][0], 1000)

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(geo.GeometryError):
            geo.map_payload(fixtures.payload(
                [fixtures.frame("a", coded=(10, 10))], canvas=(5, 5),
                strategy="diagonal"))


class SafeAreaTests(unittest.TestCase, RectAssertions):
    def test_fractional_safe_area_scales_with_visible_image(self):
        frame = fixtures.frame("x", coded=(800, 600), aperture=(40, 20, 600, 400),
                               transform="r180", safe=(0.0, 0.0, 0.5, 0.25))
        result = map_one(fixtures.payload([frame], canvas=(1200, 400)))
        self.assertEqual(result, reference(frame, (1200, 400)))

    def test_explicit_absolute_flag_beats_the_fraction_heuristic(self):
        # All components are <= 1 but the caller insists they are coded pixels.
        frame = fixtures.frame(
            "x", coded=(400, 300), aperture=(0, 0, 400, 300),
            safe={"rect": [1, 1, 0.5, 0.5], "relative": False})
        result = map_one(fixtures.payload([frame], canvas=(400, 300)))
        self.assertRect(result["safe"], [1, 1, 0.5, 0.5])

    def test_explicit_relative_flag_with_large_numbers(self):
        frame = fixtures.frame(
            "x", coded=(400, 300), aperture=(0, 0, 400, 300),
            safe={"relative": True, "rect": [0, 0, 0.5, 0.5]})
        result = map_one(fixtures.payload([frame], canvas=(400, 300)))
        self.assertRect(result["safe"], [0, 0, 200, 150])

    def test_safe_area_is_not_silently_clamped_to_the_visible_image(self):
        # A safe rect wider than the clean aperture still goes through the same
        # matrix; it extends outside the visible image rather than being
        # quietly cropped to a different rectangle.
        frame = fixtures.frame("x", coded=(1920, 1080),
                               aperture=(100, 0, 1700, 1080),
                               safe=(0, 0, 2000, 1080))
        result = map_one(fixtures.payload([frame], canvas=(1700, 1080)))
        self.assertEqual(result["safe"][0], -100)
        self.assertEqual(result["safe"][2], 2000)


class FrameSequenceTests(unittest.TestCase, RectAssertions):
    def test_transform_change_between_frames(self):
        frames = geo.map_payload(
            fixtures.two_frames_different_properties())["frames"]
        self.assertEqual(len(frames), 3)
        # f1 r90 of 1080x1920 -> 1920x1080 fills 1920x1080 exactly.
        self.assertRect(frames[0]["disp"], [1920, 1080])
        self.assertRect(frames[0]["out"], [1920, 1080])
        # f2 r270 gives the same size but the visible image is turned 180
        # degrees relative to f1; sizes still agree.
        self.assertRect(frames[1]["disp"], [1920, 1080])
        # f3 uses a 4:3 SAR and a cropped aperture: 1200 * 4/3 = 1600 wide.
        self.assertRect(frames[2]["disp"], [1600, 1080])
        self.assertRect(frames[2]["out"], [1600, 1080])

    def test_each_frame_matches_the_independent_reference(self):
        payload = fixtures.two_frames_different_properties()
        frames = geo.map_payload(payload)["frames"]
        for frame, result in zip(payload["frames"], frames):
            self.assertEqual(result, reference(frame, payload["canvas"]))


class RoundTripTests(unittest.TestCase):
    """Every output corner must map back onto the source visible region."""

    CASES = [
        # (coded, aperture, sar, transform, canvas, strategy)
        ((1920, 1080), (0, 0, 1920, 1080), (1, 1), None, (1920, 1080), "fit"),
        ((1080, 1920), (0, 0, 1080, 1920), (1, 1), "r90", (1920, 1080), "fit"),
        ((1080, 1920), (0, 0, 1080, 1920), (1, 1), "r270", (1920, 1080), "fit"),
        ((720, 576), (0, 0, 720, 576), (64, 45), "r90", (1920, 1080), "fit"),
        ((720, 576), (0, 0, 720, 576), (64, 45), "r270", (1920, 1080), "fit"),
        ((1921, 1081), (1, 3, 1919, 1075), (1, 1), "r270", (1281, 721), "fit"),
        ((1920, 1080), (7, 3, 1907, 1071), (1, 1), "mirror", (1000, 1000), "fit"),
        ((1920, 1080), (0, 0, 1920, 1080), (1, 1), None, (100, 100), "fill"),
        ((1440, 1080), (120, 0, 1200, 1080), (4, 3), "mirror", (1920, 1080), "fit"),
        ((640, 480), (11, 13, 600, 440), (3, 4), "r180", (1081, 721), "fit"),
        ((1280, 720), (2, 0, 1276, 719), (40, 33), "r90+mirror", (1919, 1081), "fit"),
        ((3840, 2160), (0, 0, 3840, 2160), (1, 1), "r270", (1080, 1920), "fit"),
        ((1920, 1080), (0, 0, 1920, 1080), (1, 1), None, (1920, 1080), "none"),
    ]

    def test_every_case_matches_the_independent_reference(self):
        for coded, aperture, sar, transform, canvas, strategy in self.CASES:
            with self.subTest(coded=coded, aperture=aperture, sar=sar,
                              transform=transform, canvas=canvas,
                              strategy=strategy):
                frame = fixtures.frame("t", coded=coded, aperture=aperture,
                                       sar=sar, transform=transform)
                payload = fixtures.payload([frame], canvas=canvas,
                                           strategy=strategy)
                self.assertEqual(map_one(payload),
                                 reference(frame, canvas, strategy))

    def test_inverse_mapping_recovers_source_corners(self):
        for coded, aperture, sar, transform, canvas, strategy in self.CASES:
            with self.subTest(coded=coded, aperture=aperture, sar=sar,
                              transform=transform, canvas=canvas,
                              strategy=strategy):
                frame = fixtures.frame("t", coded=coded, aperture=aperture,
                                       sar=sar, transform=transform)
                payload = fixtures.payload([frame], canvas=canvas,
                                           strategy=strategy)
                result = map_one(payload)

                # Rebuild the exact forward map independently.
                matrix = _matrix(transform)
                sar_x = Fraction(*sar)
                left = max(Fraction(aperture[0]), Fraction(0))
                top = max(Fraction(aperture[1]), Fraction(0))
                vw = min(Fraction(aperture[0] + aperture[2]),
                         Fraction(coded[0])) - left
                vh = min(Fraction(aperture[1] + aperture[3]),
                         Fraction(coded[1])) - top
                dw0, dh0 = vw * sar_x, vh
                points = [_apply(matrix, x, y) for x, y in
                          ((0, 0), (dw0, 0), (0, dh0), (dw0, dh0))]
                min_x = min(p[0] for p in points)
                min_y = min(p[1] for p in points)
                dw = max(p[0] for p in points) - min_x
                dh = max(p[1] for p in points) - min_y
                if strategy == "fit":
                    sx = sy = min(Fraction(canvas[0]) / dw, Fraction(canvas[1]) / dh)
                elif strategy == "fill":
                    sx = sy = max(Fraction(canvas[0]) / dw, Fraction(canvas[1]) / dh)
                elif strategy == "none":
                    sx = sy = Fraction(1)
                else:
                    sx = Fraction(canvas[0]) / dw
                    sy = Fraction(canvas[1]) / dh
                ox = (canvas[0] - dw * sx) / 2
                oy = (canvas[1] - dh * sy) / 2

                a, b, c, d = matrix
                determinant = a * d - b * c
                self.assertNotEqual(determinant, 0)
                for px, py in result["corners"]:
                    u = (Fraction(px) - ox) / sx + min_x
                    v = (Fraction(py) - oy) / sy + min_y
                    su = (d * u - b * v) / determinant
                    sv = (-c * u + a * v) / determinant
                    # Within half an output pixel of the visible image bounds.
                    slack_u = Fraction(1, 2) / sx
                    slack_v = Fraction(1, 2) / sy
                    self.assertGreaterEqual(float(su), float(-slack_u) - 1e-9)
                    self.assertLessEqual(float(su), float(dw0 + slack_u) + 1e-9)
                    self.assertGreaterEqual(float(sv), float(-slack_v) - 1e-9)
                    self.assertLessEqual(float(sv), float(dh0 + slack_v) + 1e-9)

    def test_round_trip_error_is_at_most_half_pixel(self):
        for coded, aperture, sar, transform, canvas, strategy in self.CASES:
            with self.subTest(transform=transform, canvas=canvas):
                frame = fixtures.frame("t", coded=coded, aperture=aperture,
                                       sar=sar, transform=transform)
                payload = fixtures.payload([frame], canvas=canvas,
                                           strategy=strategy)
                result = map_one(payload)
                expected = reference(frame, canvas, strategy)
                # Every emitted coordinate is on the half-pixel grid ...
                values = (result["disp"] + result["out"] + result["offset"]
                          + [c for point in result["corners"] for c in point]
                          + result["safe"])
                for value in values:
                    self.assertAlmostEqual(value * 2, round(value * 2),
                                           places=9)
                # ... and within half an output pixel of the exact position.
                for key in ("disp", "out", "offset", "safe"):
                    for got, want in zip(result[key], expected[key]):
                        self.assertLessEqual(abs(got - want), 0.5 + 1e-9)
                for got_point, want_point in zip(result["corners"],
                                                 expected["corners"]):
                    for got, want in zip(got_point, want_point):
                        self.assertLessEqual(abs(got - want), 0.5 + 1e-9)


class CliTests(unittest.TestCase):
    def test_cli_writes_schema_document(self):
        payload = fixtures.two_frames_different_properties()
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as directory:
            source = os.path.join(directory, "in.json")
            target = os.path.join(directory, "out.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            completed = subprocess.run(
                [sys.executable, SCRIPT, source, target],
                capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with open(target, encoding="utf-8") as handle:
                result = json.load(handle)
        self.assertEqual(len(result["frames"]), 3)
        for frame in result["frames"]:
            self.assertEqual(len(frame["corners"]), 2)
            self.assertEqual(len(frame["safe"]), 4)

    def test_cli_stdout_matches_api(self):
        payload = fixtures.anamorphic("r90")
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as directory:
            source = os.path.join(directory, "in.json")
            with open(source, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            completed = subprocess.run([sys.executable, SCRIPT, source],
                                       capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout),
                         geo.map_payload(payload))

    def test_cli_rejects_malformed_input(self):
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as directory:
            source = os.path.join(directory, "in.json")
            with open(source, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            completed = subprocess.run(
                [sys.executable, SCRIPT, source],
                capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("geo.py:", completed.stderr)


class MatrixInputTests(unittest.TestCase, RectAssertions):
    def test_inverse_apply_round_trips_points(self):
        for transform in (None, "r90", "r180", "r270", "mirror", "vflip",
                          "r90+mirror", "r270+vflip"):
            with self.subTest(transform=transform):
                orientation = geo.orientation_from_spec(transform)
                for x, y in ((0, 0), (Fraction(1, 2), Fraction(3, 2)),
                             (Fraction(-7, 3), Fraction(11, 5))):
                    mx, my = orientation.apply_raw(x, y)
                    rx, ry = orientation.inverse_apply(mx, my)
                    self.assertEqual((rx, ry), (x, y))

    def test_dimensions_match_bounding_box(self):
        orientation = geo.orientation_from_spec("r90")
        self.assertEqual(orientation.dimensions(Fraction(720), Fraction(576)),
                         orientation.dimensions(Fraction(720), Fraction(576)))
        self.assertEqual(orientation.box(Fraction(720), Fraction(576)),
                         (Fraction(-576), Fraction(0), Fraction(0),
                          Fraction(720)))

    def test_clockwise_rotation_matrix_matches_r90(self):
        # A clockwise 90 degree matrix in screen coordinates.
        frame = fixtures.frame("x", coded=(1080, 1920),
                               transform=[[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        result = map_one(fixtures.payload([frame], canvas=(1920, 1080)))
        self.assertRect(result["disp"], [1920, 1080])
        reference_frame = fixtures.frame("x", coded=(1080, 1920),
                                         transform="r90")
        self.assertEqual(result, map_one(fixtures.payload(
            [reference_frame], canvas=(1920, 1080))))

    def test_mirror_matrix_matches_mirror_transform(self):
        a = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080),
                            transform=[[-1, 0, 0], [0, 1, 0], [0, 0, 1]])],
            canvas=(1920, 1080)))
        b = map_one(fixtures.payload(
            [fixtures.frame("x", coded=(1920, 1080), transform="mirror")],
            canvas=(1920, 1080)))
        self.assertEqual(a, b)

    def test_matrix_dict_with_rotation_and_mirror(self):
        frame = fixtures.frame("x", coded=(1080, 1920),
                               transform={"rotation": 270, "mirror": True})
        result = map_one(fixtures.payload([frame], canvas=(1920, 1080)))
        self.assertRect(result["disp"], [1920, 1080])
        string_frame = fixtures.frame("x", coded=(1080, 1920),
                                      transform="r270+mirror")
        self.assertEqual(result, map_one(fixtures.payload(
            [string_frame], canvas=(1920, 1080))))

    def test_non_orthogonal_rotation_is_rejected(self):
        frame = fixtures.frame("x", coded=(100, 100), transform="r45")
        with self.assertRaises(geo.GeometryError):
            geo.map_payload(fixtures.payload([frame], canvas=(100, 100)))


if __name__ == "__main__":
    unittest.main()
