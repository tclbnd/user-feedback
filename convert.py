"""Offline HDR (PQ / BT.2020) to SDR (BT.709) planar frame conversion.

Two entry points share the same math:

* single frame:  ``python3 convert.py in.json out.json [--seed N] [--debug]``
* shot sequence: ``python3 convert.py in.json out.json --sequence [--seed N]
  [--debug]``

Inputs carry raw 10-bit code values in planar Y / Cb / Cr JSON objects.
The sequence wrapper adds rational time bases, PTS, durations and hard cuts.
All output is published atomically so a validation failure can never leave a
partial result behind or overwrite an existing file.
"""
import argparse
import json
import math
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Colour science constants
# ---------------------------------------------------------------------------

# BT.2020 non-constant-luminance luma coefficients.
KR2020 = 0.2627
KB2020 = 0.0593
KG2020 = 1.0 - KR2020 - KB2020

# BT.709 luma coefficients.
KR709 = 0.2126
KB709 = 0.0722
KG709 = 1.0 - KR709 - KB709

# SMPTE ST 2084 (PQ) constants.
PQ_M1 = 2610.0 / 16384.0
PQ_M2 = (2523.0 / 4096.0) * 128.0
PQ_C1 = 3424.0 / 4096.0
PQ_C2 = (2413.0 / 4096.0) * 32.0
PQ_C3 = (2392.0 / 4096.0) * 32.0
PQ_PEAK_NITS = 10000.0
NIT_BUCKETS = 10001  # integer nit buckets 0..10000

# Linear BT.2020 -> linear BT.709 gamut matrix, one row per output channel.
GAMUT_2020_TO_709 = (
    (1.660491, -0.587641, -0.072850),
    (-0.124550, 1.132900, -0.008349),
    (-0.018151, -0.100579, 1.118730),
)

# Single-frame tone mapping white parameter (nits).
SINGLE_WHITE_NITS = 203.0

# Limited-range 10-bit / 8-bit code value anchors.
Y10_OFF, Y10_SCALE = 64.0, 876.0
C10_OFF, C10_SCALE = 512.0, 896.0
FULL10_SCALE = 1023.0
Y8_OFF, Y8_SCALE = 16.0, 219.0
C8_OFF, C8_SCALE = 128.0, 224.0
Y8_MIN, Y8_MAX = 16, 235
C8_MIN, C8_MAX = 16, 240

VALID_SITING = ("center", "left")
PLANE_ID = {"y": 0, "cb": 1, "cr": 2}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _is_int(value):
    # bool is a subclass of int but is never a valid count, PTS or duration.
    return type(value) is int


def validate_plane_shapes(frame):
    """Validate the raw 10-bit planar object and return (width, height)."""
    w, h = frame.get("width"), frame.get("height")
    if not _is_int(w) or not _is_int(h) or w < 1 or h < 1:
        raise ValueError("invalid frame dimensions")
    cw, ch = (w + 1) // 2, (h + 1) // 2
    for plane, pw, ph in (("y", w, h), ("cb", cw, ch), ("cr", cw, ch)):
        rows = frame.get(plane)
        if not isinstance(rows, list) or len(rows) != ph:
            raise ValueError("invalid " + plane + " plane shape")
        for row in rows:
            if not isinstance(row, list) or len(row) != pw:
                raise ValueError("invalid " + plane + " plane shape")
            for sample in row:
                if not _is_int(sample) or not 0 <= sample <= 1023:
                    raise ValueError("invalid " + plane + " sample")
    return w, h


def validate_metadata(metadata, allow_full_range):
    """Reject missing or conflicting colour declarations."""
    if not isinstance(metadata, dict):
        raise ValueError("missing frame metadata")
    fixed = (
        ("primaries", "bt2020"),
        ("transfer", "pq"),
        ("matrix", "bt2020nc"),
    )
    for key, want in fixed:
        if key not in metadata:
            raise ValueError("missing metadata declaration: " + key)
        if metadata[key] != want:
            raise ValueError("conflicting metadata declaration: " + key)
    if metadata.get("siting") not in VALID_SITING:
        raise ValueError("conflicting metadata declaration: siting")
    allowed_ranges = ("limited", "full") if allow_full_range else ("limited",)
    if metadata.get("range") not in allowed_ranges:
        raise ValueError("conflicting metadata declaration: range")
    return {
        "primaries": "bt2020",
        "transfer": "pq",
        "matrix": "bt2020nc",
        "range": metadata["range"],
        "siting": metadata["siting"],
    }


def read_frame(path):
    """Read and shape-validate a single raw plane JSON file."""
    frame = json.loads(Path(path).read_text())
    validate_plane_shapes(frame)
    return frame


# ---------------------------------------------------------------------------
# Deterministic sub-code-value dither (splitmix64, no hash randomisation)
# ---------------------------------------------------------------------------

_MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15


def _splitmix64(z):
    z = (z + _GOLDEN) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


def dither_value(seed, plane, x, y):
    """Stable dither in (-0.5, 0.5) code values from (seed, plane, coord)."""
    z = seed & _MASK64
    z = _splitmix64(z + PLANE_ID[plane] * _GOLDEN + 1)
    z = _splitmix64(z + (x + 1) * _GOLDEN)
    z = _splitmix64(z + (y + 1) * _GOLDEN)
    # 53 mantissa bits -> uniform value in [0, 1).
    unit = (z >> 11) * (1.0 / float(1 << 53))
    return unit - 0.5


# ---------------------------------------------------------------------------
# Transfer functions
# ---------------------------------------------------------------------------

def pq_eotf(value):
    """ST 2084 EOTF: signal [0, 1] -> linear nits (10000 nit peak)."""
    if value <= 0.0:
        return 0.0
    vp = value ** (1.0 / PQ_M2)
    numerator = max(vp - PQ_C1, 0.0)
    denominator = PQ_C2 - PQ_C3 * vp
    if denominator <= 0.0:
        return PQ_PEAK_NITS
    normalized = (numerator / denominator) ** (1.0 / PQ_M1)
    return PQ_PEAK_NITS * normalized


def bt709_oetf(linear):
    """BT.709 OETF: normalized linear light -> signal."""
    if linear < 0.018:
        return 4.5 * linear
    return 1.099 * linear ** 0.45 - 0.099


# ---------------------------------------------------------------------------
# Chroma siting
# ---------------------------------------------------------------------------

def _axis_table(n, chroma_n, origin):
    """Bilinear index/weight table along one axis.

    Luma sample centres live on integer coordinates.  ``origin`` is the
    fractional offset of chroma sample 0: -0.5 for centred siting, 0.0 for
    horizontally co-sited (left) siting.  Out-of-range neighbours are clamped
    to the edge sample.
    """
    table = []
    for k in range(n):
        pos = (k + origin) / 2.0
        i0 = math.floor(pos)
        weight1 = pos - i0
        a = min(max(i0, 0), chroma_n - 1)
        b = min(max(i0 + 1, 0), chroma_n - 1)
        table.append((a, b, weight1))
    return table


def _upsample_chroma(plane, w, h, htable, vtable):
    """Bilinearly upsample one chroma plane to luma resolution."""
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        j0, j1, fy = vtable[y]
        row0 = plane[j0]
        row1 = plane[j1]
        out_row = out[y]
        for x in range(w):
            i0, i1, fx = htable[x]
            w00 = (1.0 - fx) * (1.0 - fy)
            w10 = fx * (1.0 - fy)
            w01 = (1.0 - fx) * fy
            w11 = fx * fy
            out_row[x] = (
                w00 * row0[i0] + w10 * row0[i1]
                + w01 * row1[i0] + w11 * row1[i1]
            )
    return out


def _downsample_chroma(signal_grid, w, h, target_siting):
    """Average a full-resolution chroma signal over each output support."""
    cw, ch = (w + 1) // 2, (h + 1) // 2
    out = [[0.0] * cw for _ in range(ch)]
    for j in range(ch):
        ys = [y for y in (2 * j, 2 * j + 1) if y < h]
        for i in range(cw):
            if target_siting == "left":
                xs = [2 * i]  # co-sited: the left luma column always exists
            else:
                xs = [x for x in (2 * i, 2 * i + 1) if x < w]
            total = 0.0
            for y in ys:
                row = signal_grid[y]
                for x in xs:
                    total += row[x]
            out[j][i] = total / (len(xs) * len(ys))
    return out


# ---------------------------------------------------------------------------
# Decode: raw 10-bit planes -> linear BT.709 light (nits)
# ---------------------------------------------------------------------------

def _normalized_planes(frame, color_range):
    if color_range == "full":
        yn = [[v / FULL10_SCALE for v in row] for row in frame["y"]]
        cbn = [[(v - C10_OFF) / FULL10_SCALE for v in row]
               for row in frame["cb"]]
        crn = [[(v - C10_OFF) / FULL10_SCALE for v in row]
               for row in frame["cr"]]
    else:
        yn = [[(v - Y10_OFF) / Y10_SCALE for v in row] for row in frame["y"]]
        cbn = [[(v - C10_OFF) / C10_SCALE for v in row]
               for row in frame["cb"]]
        crn = [[(v - C10_OFF) / C10_SCALE for v in row]
               for row in frame["cr"]]
    return yn, cbn, crn


def decode_linear709(frame):
    """Return PQ nits (BT.2020), BT.709 nits and BT.709 luma grids.

    RGB grids are indexed [row][col][channel]; luma is a 2-D grid.
    All gamut and highlight work happens in linear light.
    """
    w, h = frame["width"], frame["height"]
    meta = frame["metadata"]
    yn, cbn, crn = _normalized_planes(frame, meta["range"])

    cw, ch = (w + 1) // 2, (h + 1) // 2
    h_origin = -0.5 if meta["siting"] == "center" else 0.0
    htable = _axis_table(w, cw, h_origin)
    vtable = _axis_table(h, ch, -0.5)
    cb_up = _upsample_chroma(cbn, w, h, htable, vtable)
    cr_up = _upsample_chroma(crn, w, h, htable, vtable)

    # BT.2020 NCL Y'Cb'Cr' -> R'G'B'.
    vr = 2.0 * (1.0 - KR2020)
    vb = 2.0 * (1.0 - KB2020)
    ug = -2.0 * KB2020 * (1.0 - KB2020) / KG2020
    vg = -2.0 * KR2020 * (1.0 - KR2020) / KG2020

    m0, m1, m2 = GAMUT_2020_TO_709
    rgb2020 = [[[0.0, 0.0, 0.0] for _ in range(w)] for _ in range(h)]
    rgb709 = [[[0.0, 0.0, 0.0] for _ in range(w)] for _ in range(h)]
    luma709 = [[0.0] * w for _ in range(h)]

    for y in range(h):
        y_row = yn[y]
        cb_row = cb_up[y]
        cr_row = cr_up[y]
        out2020_row = rgb2020[y]
        out709_row = rgb709[y]
        l_row = luma709[y]
        for x in range(w):
            yv = y_row[x]
            cbv = cb_row[x]
            crv = cr_row[x]
            rp = min(max(yv + vr * crv, 0.0), 1.0)
            gp = min(max(yv + ug * cbv + vg * crv, 0.0), 1.0)
            bp = min(max(yv + vb * cbv, 0.0), 1.0)

            r2020 = pq_eotf(rp)
            g2020 = pq_eotf(gp)
            b2020 = pq_eotf(bp)
            out2020_row[x][0] = r2020
            out2020_row[x][1] = g2020
            out2020_row[x][2] = b2020

            r = m0[0] * r2020 + m0[1] * g2020 + m0[2] * b2020
            g = m1[0] * r2020 + m1[1] * g2020 + m1[2] * b2020
            b = m2[0] * r2020 + m2[1] * g2020 + m2[2] * b2020
            out709_row[x][0] = r
            out709_row[x][1] = g
            out709_row[x][2] = b
            l_row[x] = KR709 * r + KG709 * g + KB709 * b

    return rgb2020, rgb709, luma709


# ---------------------------------------------------------------------------
# Encode: linear BT.709 -> 8-bit limited-range planes
# ---------------------------------------------------------------------------

def _quantize(base_code, low, high, seed, plane, x, y):
    if seed is not None:
        base_code += dither_value(seed, plane, x, y)
    return min(max(int(math.floor(base_code + 0.5)), low), high)


def encode_sdr(rgb709, luma709, white_nits, w, h, target_siting, seed):
    """Tone map, apply OETF, matrix and quantize one frame."""
    y_sig = [[0.0] * w for _ in range(h)]
    cb_sig = [[0.0] * w for _ in range(h)]
    cr_sig = [[0.0] * w for _ in range(h)]
    mapped = [[[0.0, 0.0, 0.0] for _ in range(w)] for _ in range(h)]

    cb_scale = 2.0 * (1.0 - KB709)
    cr_scale = 2.0 * (1.0 - KR709)

    for y in range(h):
        in_row = rgb709[y]
        l_row = luma709[y]
        ys_row = y_sig[y]
        cb_row = cb_sig[y]
        cr_row = cr_sig[y]
        map_row = mapped[y]
        for x in range(w):
            r_lin, g_lin, b_lin = in_row[x]
            denominator = white_nits + max(l_row[x], 0.0)
            r_m = min(max(r_lin / denominator, 0.0), 1.0)
            g_m = min(max(g_lin / denominator, 0.0), 1.0)
            b_m = min(max(b_lin / denominator, 0.0), 1.0)
            map_row[x][0] = r_m
            map_row[x][1] = g_m
            map_row[x][2] = b_m

            r = bt709_oetf(r_m)
            g = bt709_oetf(g_m)
            b = bt709_oetf(b_m)
            yv = KR709 * r + KG709 * g + KB709 * b
            ys_row[x] = yv
            cb_row[x] = (b - yv) / cb_scale
            cr_row[x] = (r - yv) / cr_scale

    cb_down = _downsample_chroma(cb_sig, w, h, target_siting)
    cr_down = _downsample_chroma(cr_sig, w, h, target_siting)
    cw, ch = (w + 1) // 2, (h + 1) // 2

    y_plane = [
        [
            _quantize(Y8_OFF + Y8_SCALE * y_sig[y][x], Y8_MIN, Y8_MAX,
                      seed, "y", x, y)
            for x in range(w)
        ]
        for y in range(h)
    ]
    cb_plane = [
        [
            _quantize(C8_OFF + C8_SCALE * cb_down[j][i], C8_MIN, C8_MAX,
                      seed, "cb", i, j)
            for i in range(cw)
        ]
        for j in range(ch)
    ]
    cr_plane = [
        [
            _quantize(C8_OFF + C8_SCALE * cr_down[j][i], C8_MIN, C8_MAX,
                      seed, "cr", i, j)
            for i in range(cw)
        ]
        for j in range(ch)
    ]
    return y_plane, cb_plane, cr_plane, mapped


def _output_metadata(siting):
    return {
        "primaries": "bt709",
        "transfer": "bt709",
        "matrix": "bt709",
        "range": "limited",
        "siting": siting,
    }


def convert(frame, seed=None, debug=False):
    """Single-frame conversion preserving the historical CLI contract."""
    meta = validate_metadata(frame.get("metadata"), allow_full_range=False)
    frame = dict(frame)
    frame["metadata"] = meta
    w, h = frame["width"], frame["height"]

    rgb2020, rgb709, luma709 = decode_linear709(frame)
    y_plane, cb_plane, cr_plane, mapped = encode_sdr(
        rgb709, luma709, SINGLE_WHITE_NITS, w, h, meta["siting"], seed
    )
    out = {
        "width": w,
        "height": h,
        "metadata": _output_metadata(meta["siting"]),
        "y": y_plane,
        "cb": cb_plane,
        "cr": cr_plane,
    }
    if debug:
        out["debug"] = {
            "rgb2020_nits": rgb2020,
            "rgb709_nits": rgb709,
            "mapped_linear709": mapped,
        }
    return out


# ---------------------------------------------------------------------------
# Shot sequence: validation, weighted statistics, two-pass conversion
# ---------------------------------------------------------------------------

def _validate_sequence(data):
    if not isinstance(data, dict):
        raise ValueError("sequence input must be a JSON object")
    time_base = data.get("time_base")
    if (not isinstance(time_base, list) or len(time_base) != 2
            or not all(_is_int(v) and v > 0 for v in time_base)):
        raise ValueError("invalid time_base")
    target_siting = data.get("target_siting")
    if target_siting not in VALID_SITING:
        raise ValueError("invalid target_siting")
    raw_frames = data.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ValueError("frames must be a non-empty list")

    prepared = []
    prev_pts = None
    prev_end = None
    shot_dims = None
    for index, entry in enumerate(raw_frames):
        if not isinstance(entry, dict):
            raise ValueError("invalid frame entry at index %d" % index)
        pts = entry.get("pts")
        duration = entry.get("duration")
        if not _is_int(pts):
            raise ValueError("invalid pts at index %d" % index)
        if not _is_int(duration) or duration <= 0:
            raise ValueError("invalid duration at index %d" % index)
        cut = entry.get("cut", False)
        if not isinstance(cut, bool):
            raise ValueError("invalid cut flag at index %d" % index)
        raw_frame = entry.get("frame")
        if not isinstance(raw_frame, dict):
            raise ValueError("missing plane frame at index %d" % index)
        w, h = validate_plane_shapes(raw_frame)
        meta = validate_metadata(raw_frame.get("metadata"),
                                 allow_full_range=True)
        frame = dict(raw_frame)
        frame["metadata"] = meta

        if prev_pts is not None:
            if pts <= prev_pts:
                raise ValueError("pts must be strictly increasing")
            if prev_end > pts:
                raise ValueError(
                    "frame durations overlap at index %d" % index
                )
        is_shot_start = index == 0 or cut
        if is_shot_start:
            shot_dims = (w, h)
        elif (w, h) != shot_dims:
            raise ValueError(
                "dimensions may only change at a hard cut (index %d)" % index
            )

        prepared.append({
            "pts": pts,
            "duration": duration,
            "cut": cut,
            "shot_start": is_shot_start,
            "frame": frame,
        })
        prev_pts = pts
        prev_end = pts + duration
    return time_base, target_siting, prepared


def _finalize_shot(shot):
    total = shot["pixel_ticks"]
    cumulative = 0
    p95 = NIT_BUCKETS - 1
    for nit, weight in enumerate(shot["histogram"]):
        cumulative += weight
        if cumulative * 100 >= total * 95:
            p95 = nit
            break
    shot["p95_nits"] = p95
    half = p95 / 2.0
    shot["white_nits"] = (
        SINGLE_WHITE_NITS if half <= SINGLE_WHITE_NITS else half
    )
    del shot["histogram"]
    del shot["entries"]


def _shot_statistics(prepared):
    """First pass: duration-weighted integer-nit histogram per shot."""
    shots = []
    current = None
    for entry in prepared:
        if entry["shot_start"]:
            if current is not None:
                _finalize_shot(current)
                shots.append(current)
            current = {
                "start_pts": entry["pts"],
                "histogram": [0] * NIT_BUCKETS,
                "pixel_ticks": 0,
                "entries": [],
            }
        frame = entry["frame"]
        w, h = frame["width"], frame["height"]
        _, _, luma709 = decode_linear709(frame)
        weight = entry["duration"]
        hist = current["histogram"]
        for row in luma709:
            for value in row:
                bucket = int(math.floor(value + 0.5))
                if bucket < 0:
                    bucket = 0
                elif bucket >= NIT_BUCKETS:
                    bucket = NIT_BUCKETS - 1
                hist[bucket] += weight
        current["pixel_ticks"] += weight * w * h
        current["entries"].append(entry)
    _finalize_shot(current)
    shots.append(current)
    return shots


def convert_sequence(data, seed=None, debug=False):
    time_base, target_siting, prepared = _validate_sequence(data)

    # Pass 1: bounded, streamable per-shot statistics.
    shots = _shot_statistics(prepared)

    # Pass 2: every frame in a shot shares one white parameter, so static
    # pixels cannot move when neighbouring frames contain brief highlights.
    shot_by_start = {s["start_pts"]: s for s in shots}
    out_frames = []
    current_start = None
    for entry in prepared:
        if entry["shot_start"]:
            current_start = entry["pts"]
        white_nits = shot_by_start[current_start]["white_nits"]
        frame = entry["frame"]
        w, h = frame["width"], frame["height"]
        rgb2020, rgb709, luma709 = decode_linear709(frame)
        y_plane, cb_plane, cr_plane, mapped = encode_sdr(
            rgb709, luma709, white_nits, w, h, target_siting, seed
        )
        converted = {
            "width": w,
            "height": h,
            "metadata": _output_metadata(target_siting),
            "y": y_plane,
            "cb": cb_plane,
            "cr": cr_plane,
        }
        if debug:
            converted["debug"] = {
                "rgb2020_nits": rgb2020,
                "rgb709_nits": rgb709,
                "mapped_linear709": mapped,
            }
        out_frames.append({
            "pts": entry["pts"],
            "duration": entry["duration"],
            "shot_start_pts": current_start,
            "frame": converted,
        })

    return {
        "time_base": time_base,
        "frames": out_frames,
        "shots": [
            {
                "start_pts": s["start_pts"],
                "pixel_ticks": s["pixel_ticks"],
                "p95_nits": s["p95_nits"],
                "white_nits": s["white_nits"],
            }
            for s in shots
        ],
    }


# ---------------------------------------------------------------------------
# Atomic publishing and CLI
# ---------------------------------------------------------------------------

def write_json_atomic(path, payload):
    """Serialize and replace the destination only after a complete write."""
    path = Path(path)
    tmp = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, allow_nan=False))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Offline HDR PQ/BT.2020 to SDR BT.709 conversion")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--sequence", action="store_true")
    args = parser.parse_args()
    try:
        if args.sequence:
            data = json.loads(args.input.read_text())
            result = convert_sequence(data, args.seed, args.debug)
        else:
            result = convert(read_frame(args.input), args.seed, args.debug)
        # Validation is fully complete before any filesystem mutation.
        write_json_atomic(args.output, result)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
