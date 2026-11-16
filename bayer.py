#!/usr/bin/env python3
"""Bayer bad-pixel correction + bilinear demosaic.

Usage: python3 bayer.py input.json output.json [--diag diag.json]

``pattern`` names the CFA of the sensor on which ``raw`` was captured.  For a
plain frame the crop origin is (0, 0) and pixel (y, x) is the site given by
PATTERNS[pattern][y & 1][x & 1].  When ``crop_origin`` says the frame starts
at an odd sensor row/column, the phase table is rotated accordingly, so the
sampling layout, the bad-pixel neighbourhood and the interpolation phase all
stay correct after a crop.
"""

import json
import sys

# (y & 1, x & 1) -> colour, for each supported CFA phase.
PATTERNS = {
    "RGGB": (("R", "G"), ("G", "B")),
    "BGGR": (("B", "G"), ("G", "R")),
    "GRBG": (("G", "R"), ("B", "G")),
    "GBRG": (("G", "B"), ("R", "G")),
}

# Same-colour sites at Chebyshev distance 2.  Every offset is even in both
# axes, so a candidate always shares the CFA phase (and hence colour) of the
# centre pixel; the colour check below is kept explicit for clarity.
BAD_NEIGHBOURS = (
    (-2, 0), (2, 0), (0, -2), (0, 2),
    (-2, -2), (-2, 2), (2, -2), (2, 2),
)

ORTHOGONAL = ((0, -1), (0, 1), (-1, 0), (1, 0))
DIAGONAL = ((-1, -1), (-1, 1), (1, -1), (1, 1))

CHANNELS = ("R", "G", "B")


def pattern_table(name):
    """Return the 2x2 CFA phase table for ``name``."""
    key = str(name).strip().upper()
    if key not in PATTERNS:
        raise ValueError("unsupported CFA pattern: %r" % (name,))
    return PATTERNS[key]


def site(table, y, x):
    """Colour of pixel (y, x) in the cropped frame."""
    return table[y % 2][x % 2]


ORIGIN_KEYS = ("crop_origin", "sensor_origin", "origin")


def _origin(data):
    """Read the optional crop origin, i.e. the sensor coordinate of the
    cropped frame's pixel (0, 0).

    Accepted forms: [y, x] / (y, x) (same order as ``bad_pixels``),
    {"y": y, "x": x}, or the split fields ``origin_y``/``origin_x``.
    Returns (0, 0) when no origin is supplied, which reproduces the plain
    README behaviour (``pattern`` describes pixel (0,0) of ``raw``).
    """
    value = None
    for key in ORIGIN_KEYS:
        if key in data and data[key] is not None:
            value = data[key]
            break

    y = data.get("origin_y")
    x = data.get("origin_x")
    if y is not None or x is not None:
        return int(y or 0), int(x or 0)
    if value is None:
        return 0, 0
    if isinstance(value, dict):
        return int(value.get("y", 0)), int(value.get("x", 0))
    seq = list(value)
    if len(seq) < 2:
        raise ValueError("crop origin needs two coordinates: %r" % (value,))
    return int(seq[0]), int(seq[1])


def shift_table(table, y0, x0):
    """CFA table as seen by a frame whose sensor origin is (y0, x0).

    ``table`` describes the sensor; when a crop starts at an odd row or
    column the frame sees the phase shifted by the origin parity, which is
    exactly what must be used for every neighbourhood test.
    """
    y0, x0 = y0 % 2, x0 % 2
    return tuple(tuple(table[(y0 + dy) % 2][(x0 + dx) % 2]
                       for dx in (0, 1)) for dy in (0, 1))


def _number(value):
    """Best-effort conversion of a JSON scalar to a number."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _per_channel(value, default):
    """Interpret a level field as a scalar or as per-channel RGB levels.

    Accepted forms:
      * number                          -> same level for R, G, B
      * [r, g, b] / (r, g, b)           -> R, G, B order
      * {"R": r, "G": g, "B": b}        -> channel names (any case)
      * {"Gr": g1, "Gb": g2, ...}       -> greens averaged
    Missing channels fall back to ``default`` (or to the first usable value).
    """
    if value is None:
        return {c: default for c in CHANNELS}

    if isinstance(value, dict):
        found = {}
        lowered = {}
        for k, v in value.items():
            num = _number(v)
            if num is None:
                continue
            lowered[str(k).strip().lower()] = num
            try:
                idx = int(k)
            except (TypeError, ValueError):
                idx = None
            if idx is not None and 0 <= idx <= 2:
                found[CHANNELS[idx]] = num
        for c in CHANNELS:
            if c in found:
                continue
            if c.lower() in lowered:
                found[c] = lowered[c.lower()]
        if "G" not in found:
            greens = [lowered[k] for k in ("gr", "gb", "g_r", "g_b")
                      if k in lowered]
            if greens:
                found["G"] = sum(greens) / len(greens)
        fallback = default
        if fallback is None and found:
            fallback = next(iter(found.values()))
        if fallback is None:
            fallback = 0
        return {c: found.get(c, fallback) for c in CHANNELS}

    if isinstance(value, (list, tuple)):
        nums = [_number(v) for v in value]
        nums = [n for n in nums if n is not None]
        if len(nums) == 1:
            return {c: nums[0] for c in CHANNELS}
        if len(nums) >= 3:
            return {c: nums[i] for i, c in enumerate(CHANNELS)}
        if nums:
            return {c: nums[0] for c in CHANNELS}

    num = _number(value)
    if num is None:
        num = 0 if default is None else default
    return {c: num for c in CHANNELS}


def _median(values):
    """Upper median, matching the package convention for even counts."""
    values = sorted(values)
    return values[len(values) // 2]


def correct_bad_pixels(raw, h, w, table, bad_pixels):
    """Replace each marked bad pixel by the median of its same-colour
    neighbours at Chebyshev distance 2 (average when fewer than 3 exist).

    Returns (corrected, uncorrectable); ``raw`` is modified in place, in
    input order, and the original value of every corrected pixel is kept in
    the ``corrected`` record.
    """
    corrected = []
    uncorrectable = []
    seen = set()

    for entry in bad_pixels:
        if isinstance(entry, dict):
            y, x = entry.get("y"), entry.get("x")
        else:
            try:
                y, x = entry[0], entry[1]
            except (TypeError, IndexError, KeyError):
                continue
        if y is None or x is None:
            continue
        y, x = int(y), int(x)
        if not (0 <= y < h and 0 <= x < w):
            continue
        if (y, x) in seen:
            # A coordinate listed twice must not be "corrected" twice (the
            # second pass would treat the repaired value as the original).
            continue
        seen.add((y, x))

        colour = site(table, y, x)
        values = []
        for dy, dx in BAD_NEIGHBOURS:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and site(table, ny, nx) == colour:
                values.append(raw[ny][nx])

        old = raw[y][x]
        if not values:
            # No same-colour reference: leave the sample untouched and
            # report it, so the raw diagnostic sample survives.
            uncorrectable.append([y, x])
            continue
        if len(values) >= 3:
            new = _median(values)
        else:
            new = round(sum(values) / len(values))
        new = int(new)
        raw[y][x] = new
        corrected.append([y, x, int(old), new])

    return corrected, uncorrectable


def _mean_neighbours(raw, h, w, table, y, x, want, offsets):
    values = []
    for dy, dx in offsets:
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and site(table, ny, nx) == want:
            values.append(raw[ny][nx])
    return values


def demosaic(raw, h, w, table):
    """Bilinear demosaic in sensor units.

    A pixel keeps its own (corrected) sample.  Every missing channel is the
    mean of in-bounds orthogonal neighbours of that colour; only when no
    such neighbour exists (image borders, R/B at the opposite site) up to
    four diagonal neighbours are used.  Means are kept as floats; rounding
    happens once, after black-level subtraction and scaling.
    """
    rgb = []
    for y in range(h):
        row = []
        for x in range(w):
            own = site(table, y, x)
            pixel = {"R": 0.0, "G": 0.0, "B": 0.0}
            pixel[own] = float(raw[y][x])
            for want in CHANNELS:
                if want == own:
                    continue
                values = _mean_neighbours(raw, h, w, table, y, x, want,
                                          ORTHOGONAL)
                if not values:
                    values = _mean_neighbours(raw, h, w, table, y, x, want,
                                              DIAGONAL)
                pixel[want] = (sum(values) / float(len(values))
                               if values else 0.0)
            row.append([pixel["R"], pixel["G"], pixel["B"]])
        rgb.append(row)
    return rgb


def normalize(rgb, black, white):
    """Scale sensor units to 0..255 with per-channel black/white levels.

    Whitening is ``round(clamp(v - black, 0, white - black) * 255 /
    (white - black))`` per channel.  ``black``/``white`` are dicts keyed
    R/G/B.  Samples below black or above white are clamped, i.e. saturated
    samples do not wrap around or drag the interpolation of their neighbours
    out of range.
    """
    out = []
    for row in rgb:
        out_row = []
        for pixel in row:
            scaled = []
            for i, c in enumerate(CHANNELS):
                b = black[c]
                span = white[c] - b
                v = pixel[i] - b
                if span <= 0:
                    scaled.append(0)
                    continue
                if v < 0:
                    v = 0
                elif v > span:
                    v = span
                scaled.append(int(round(v * 255.0 / span)))
            out_row.append(scaled)
        out.append(out_row)
    return out


def run(data, diagnostics=None):
    """Process a decoded input document and return the output document.

    ``diagnostics`` may be a dict; when given it is filled with the
    intermediate, unprocessed maps (raw CFA before/after correction, defect
    mask, and the un-normalised demosaic) so the offline frames used for
    debugging are preserved alongside the final result.
    """
    w = int(data["width"])
    h = int(data["height"])
    table = pattern_table(data["pattern"])
    oy, ox = _origin(data)
    if (oy % 2, ox % 2) != (0, 0):
        # Cropped frame with an odd sensor origin: the CFA phase of the
        # stored samples is shifted, so every sampling test uses the
        # shifted table.  Coordinates in ``bad_pixels`` are frame-local.
        table = shift_table(table, oy, ox)

    raw_in = data["raw"]
    if len(raw_in) < h:
        raise ValueError("raw has %d rows, expected %d" % (len(raw_in), h))
    raw = []
    for y in range(h):
        row = list(raw_in[y])
        if len(row) < w:
            raise ValueError("raw row %d has %d columns, expected %d"
                             % (y, len(row), w))
        raw.append([int(v) for v in row[:w]])

    black = _per_channel(data.get("black_level", 0), 0)
    white = _per_channel(data.get("white_level", 255), 255)

    raw_original = [row[:] for row in raw]
    bad_pixels = data.get("bad_pixels") or []
    corrected, uncorrectable = correct_bad_pixels(
        raw, h, w, table, bad_pixels
    )
    sensor = demosaic(raw, h, w, table)
    rgb = normalize(sensor, black, white)

    if diagnostics is not None:
        corrected_set = {(y, x) for y, x, _, _ in corrected}
        uncorrectable_set = {(y, x) for y, x in uncorrectable}
        marked = set()
        for entry in bad_pixels:
            try:
                yy, xx = (entry.get("y"), entry.get("x")) \
                    if isinstance(entry, dict) else (entry[0], entry[1])
            except (TypeError, IndexError, KeyError):
                continue
            if yy is None or xx is None:
                continue
            yy, xx = int(yy), int(xx)
            if 0 <= yy < h and 0 <= xx < w:
                marked.add((yy, xx))
        diagnostics.update({
            "cfa": [list(r) for r in table],
            "black_level": dict(black),
            "white_level": dict(white),
            "raw_original": raw_original,
            "raw_corrected": [row[:] for row in raw],
            # Unprocessed (never corrected) defect map: 1 = marked bad and
            # left untouched because no same-colour reference existed.
            "uncorrectable_mask": [
                [1 if (y, x) in uncorrectable_set else 0 for x in range(w)]
                for y in range(h)
            ],
            "bad_mask": [
                [1 if (y, x) in marked else 0 for x in range(w)]
                for y in range(h)
            ],
            "corrected_mask": [
                [1 if (y, x) in corrected_set else 0 for x in range(w)]
                for y in range(h)
            ],
            "demosaic_sensor": sensor,
        })

    flat = [pixel for row in rgb for pixel in row]
    return {
        "rgb": flat,
        "corrected": corrected,
        "uncorrectable": uncorrectable,
    }


def main(argv):
    args = [a for a in argv[1:]]
    diag_path = None
    if "--diag" in args:
        i = args.index("--diag")
        try:
            diag_path = args[i + 1]
        except IndexError:
            sys.stderr.write("--diag requires a path\n")
            return 2
        del args[i:i + 2]
    if len(args) != 2:
        sys.stderr.write(
            "usage: bayer.py input.json output.json [--diag diag.json]\n")
        return 2

    with open(args[0], "r") as fh:
        data = json.load(fh)
    diagnostics = {} if diag_path else None
    result = run(data, diagnostics)
    with open(args[1], "w") as fh:
        json.dump(result, fh, separators=(",", ":"))
    if diag_path:
        with open(diag_path, "w") as fh:
            json.dump(diagnostics, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
