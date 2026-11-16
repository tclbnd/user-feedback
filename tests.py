#!/usr/bin/env python3
"""Tests for bayer.py (bad-pixel correction + bilinear demosaic).

Scenes: colour bars, slanted edge, a linear ramp, single bad pixel, dense bad
pixels on a hard edge.  Every scene is run for all four CFA patterns and with
odd crop origins, and the pixel output is compared against an independent
reference implementation written straight from README.md.

Run: python3 tests.py
"""

import json
import os
import subprocess
import sys
import tempfile

import bayer

HERE = os.path.dirname(os.path.abspath(__file__))
PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")

# ---------------------------------------------------------------------------
# Independent reference implementation (deliberately written separately from
# bayer.py: explicit index arithmetic, per-site channel maps, no shared
# helpers).
# ---------------------------------------------------------------------------


def ref_table(pattern):
    y0 = pattern[0]
    x0 = pattern[1]
    y1 = pattern[2]
    x1 = pattern[3]
    return ((y0, x0), (y1, x1))


def ref_run(data):
    w = data["width"]
    h = data["height"]
    tab = ref_table(data["pattern"])
    raw = [list(r) for r in data["raw"]]

    black = data.get("black_level", 0)
    white = data.get("white_level", 255)
    if isinstance(black, dict):
        black = [black.get(c, black.get(c.lower(), 0)) for c in "RGB"]
    elif isinstance(black, (list, tuple)):
        black = list(black)
    else:
        black = [black] * 3
    if isinstance(white, dict):
        white = [white.get(c, white.get(c.lower(), 255)) for c in "RGB"]
    elif isinstance(white, (list, tuple)):
        white = list(white)
    else:
        white = [white] * 3

    def colour(y, x):
        return tab[y & 1][x & 1]

    corr = []
    bad = []
    for y, x in data.get("bad_pixels") or []:
        # Chebyshev distance 2, same colour only.
        same = []
        for dy, dx in ((-2, 0), (2, 0), (0, -2), (0, 2),
                       (-2, -2), (-2, 2), (2, -2), (2, 2)):
            ny = y + dy
            nx = x + dx
            if 0 <= ny < h and 0 <= nx < w and colour(ny, nx) == colour(y, x):
                same.append(raw[ny][nx])
        if not same:
            bad.append([y, x])
            continue
        same.sort()
        if len(same) >= 3:
            new = same[len(same) // 2]
        else:
            new = int(round(sum(same) / float(len(same))))
        old = raw[y][x]
        raw[y][x] = new
        corr.append([y, x, old, new])

    out = []
    for y in range(h):
        for x in range(w):
            own = colour(y, x)
            vals = {own: raw[y][x]}
            for want in "RGB":
                if want == own:
                    continue
                acc = []
                for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and colour(ny, nx) == want:
                        acc.append(raw[ny][nx])
                if not acc:
                    for dy, dx in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and colour(ny, nx) == want:
                            acc.append(raw[ny][nx])
                vals[want] = sum(acc) / float(len(acc)) if acc else 0.0
            px = []
            for i, c in enumerate("RGB"):
                span = float(white[i] - black[i])
                v = vals[c] - black[i]
                v = min(max(v, 0.0), span) if span > 0 else 0.0
                px.append(int(round(v * 255.0 / span)) if span > 0 else 0)
            out.append(px)
    return {"rgb": out, "corrected": corr, "uncorrectable": bad}


# ---------------------------------------------------------------------------
# Scene synthesis.
# ---------------------------------------------------------------------------


def mosaic(rgb_rows, pattern):
    """Sample a ground-truth RGB image at the CFA sites of ``pattern``."""
    tab = ref_table(pattern)
    ch = {"R": 0, "G": 1, "B": 2}
    raw = []
    for y, row in enumerate(rgb_rows):
        out = []
        for x, px in enumerate(row):
            out.append(int(round(px[ch[tab[y & 1][x & 1]]])))
        raw.append(out)
    return raw


def ramp_scene(w, h):
    """Linear ramp: bilinear interpolation on it is exact in the interior."""
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            row.append([20 + 3 * x + 2 * y, 60 + 2 * x + 2 * y,
                        200 - 2 * x - 1 * y])
        rows.append(row)
    return rows


def bars_scene(w, h):
    """8 SMPTE-like vertical bars, 1/8 width each."""
    colours = [(0, 0, 0), (255, 255, 255), (255, 255, 0), (0, 255, 255),
               (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            idx = min(len(colours) - 1, x * len(colours) // w)
            row.append(list(colours[idx]))
        rows.append(row)
    return rows


def edge_scene(w, h, fg=(240, 30, 30), bg=(30, 30, 240)):
    """Slanted edge: x > 0.6*y + w/3 is foreground."""
    rows = []
    for y in range(h):
        row = []
        cut = 0.6 * y + w / 3.0
        for x in range(w):
            row.append(list(fg if x >= cut else bg))
        rows.append(row)
    return rows


def doc(pattern, raw, bad=None, black=0, white=255):
    h = len(raw)
    w = len(raw[0])
    return {"pattern": pattern, "width": w, "height": h, "raw": raw,
            "bad_pixels": bad or [], "black_level": black, "white_level": white}


def crop(raw, y0, x0, hh, ww):
    return [row[x0:x0 + ww] for row in raw[y0:y0 + hh]]


def crop_phase(pattern, y0, x0):
    """Pattern of a sub-frame whose origin (y0,x0) is odd in places.

    The CFA phase of the crop is the full-frame pattern shifted by the crop
    origin; this is what the pipeline must assume for cropped frames.
    """
    tab = ref_table(pattern)
    return "".join(tab[(y0 + dy) & 1][(x0 + dx) & 1]
                   for dy in (0, 1) for dx in (0, 1))


# ---------------------------------------------------------------------------
# Runner helpers.
# ---------------------------------------------------------------------------


class Failure(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Failure(msg)


def run_pipeline(data):
    return bayer.run(data)


def compare_output(got, want, label, skip=lambda y, x: False, tol=0):
    check("rgb" in got and "rgb" in want, label + ": missing rgb")
    n = len(want["rgb"])
    check(len(got["rgb"]) == n,
          label + ": %d pixels, expected %d" % (len(got["rgb"]), n))
    h = got.get("_h", want.get("_h"))
    w = got.get("_w", want.get("_w"))
    bad = 0
    for i in range(n):
        y, x = divmod(i, w)
        if skip(y, x):
            continue
        for c in range(3):
            if abs(got["rgb"][i][c] - want["rgb"][i][c]) > tol:
                bad += 1
                if bad <= 5:
                    print("    %s mismatch at (%d,%d) ch%d: %r != %r"
                          % (label, y, x, c, got["rgb"][i], want["rgb"][i]))
    check(bad == 0, "%s: %d channel mismatches (tol=%d)" % (label, bad, tol))
    check(got["corrected"] == want["corrected"],
          label + ": corrected list differs:\n      got %r\n      want %r"
          % (got["corrected"], want["corrected"]))
    check(got["uncorrectable"] == want["uncorrectable"],
          label + ": uncorrectable list differs")


def attach_dims(result, w, h):
    result = dict(result)
    result["_w"] = w
    result["_h"] = h
    return result


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------

RESULTS = []


def test(name):
    def deco(fn):
        try:
            fn()
        except Failure as exc:
            RESULTS.append((name, False, str(exc)))
            print("FAIL  %s\n      %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001 - report, do not abort suite
            RESULTS.append((name, False, "%s: %s" % (type(exc).__name__, exc)))
            print("ERROR %s\n      %s: %s" % (name, type(exc).__name__, exc))
        else:
            RESULTS.append((name, True, ""))
            print("ok    %s" % name)
        return fn
    return deco


def _site_colours(pattern, h, w):
    tab = ref_table(pattern)
    return [[tab[y & 1][x & 1] for x in range(w)] for y in range(h)]


@test("CFA phase table matches README for all four patterns")
def t_phase_table():
    expect = {
        "RGGB": ["RG", "GB"],
        "BGGR": ["BG", "GR"],
        "GRBG": ["GR", "BG"],
        "GBRG": ["GB", "RG"],
    }
    for pattern in PATTERNS:
        tab = bayer.pattern_table(pattern)
        got = ["".join(r) for r in tab]
        check(got == expect[pattern],
              "%s phase %r != %r" % (pattern, got, expect[pattern]))
        # Relabelling must survive odd origins: site(y,x) == tab[y%2][x%2].
        for y in range(-3, 4):
            for x in range(-3, 4):
                check(bayer.site(tab, y, x) == tab[y & 1][x & 1],
                      "%s phase drift at (%d,%d)" % (pattern, y, x))


@test("pipeline keeps the CFA sampling phase (own channel == raw sample)")
def t_sampling_phase():
    w, h = 33, 27
    truth = ramp_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        diag = {}
        out = bayer.run(doc(pattern, raw), diag)
        check("demosaic_sensor" in diag, pattern + ": no sensor diagnostic")
        check(diag["raw_corrected"] == raw,
              pattern + ": correction touched an image without defects")
        for y in range(h):
            for x in range(w):
                own = ref_table(pattern)[y & 1][x & 1]
                val = diag["demosaic_sensor"][y][x]["RGB".index(own)]
                check(abs(val - raw[y][x]) < 1e-9,
                      "%s: own channel at (%d,%d) is %r, raw is %r"
                      % (pattern, y, x, val, raw[y][x]))


@test("demosaic output matches reference for ramp, all patterns")
def t_ramp_reference():
    w, h = 31, 23
    truth = ramp_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        got = run_pipeline(doc(pattern, raw))
        want = ref_run(doc(pattern, raw))
        compare_output(attach_dims(got, w, h), attach_dims(want, w, h),
                       "ramp " + pattern)


@test("linear ramp is reproduced exactly in the image interior")
def t_ramp_exact():
    w, h = 31, 23
    truth = ramp_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        out = run_pipeline(doc(pattern, raw))
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                got = out["rgb"][y * w + x]
                for c in range(3):
                    if abs(got[c] - truth[y][x][c]) > 1:
                        raise Failure(
                            "%s ramp (%d,%d) ch%d: %r != %r"
                            % (pattern, y, x, c, got, truth[y][x]))


@test("colour bars: bar interiors are exact, all patterns")
def t_bars():
    w, h = 64, 32
    truth = bars_scene(w, h)
    bar = w // 8
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        out = run_pipeline(doc(pattern, raw))
        for y in range(2, h - 2):
            for x in range(w):
                # Stay inside a bar: skip 3 px next to each bar boundary.
                if x % bar < 3 or x % bar > bar - 4:
                    continue
                got = out["rgb"][y * w + x]
                if got != truth[y][x]:
                    raise Failure("%s bars (%d,%d): %r != %r"
                                  % (pattern, y, x, got, truth[y][x]))


@test("slanted edge: edge position preserved, flat areas unpolluted")
def t_edge():
    w, h = 48, 32
    truth = edge_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        out = run_pipeline(doc(pattern, raw))
        # Detect the edge crossing per row from the green-ish output and
        # compare with ground truth.
        for y in range(2, h - 2):
            cut = 0.6 * y + w / 3.0
            for x in range(w):
                got = out["rgb"][y * w + x]
                if abs(x - cut) < 3.5:
                    continue
                if got != truth[y][x]:
                    raise Failure("%s edge (%d,%d): %r != %r"
                                  % (pattern, y, x, got, truth[y][x]))


@test("single bad pixel: same-colour ring median, no leak beyond one pixel")
def t_single_bad():
    w, h = 17, 13
    truth = ramp_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        for by, bx in ((6, 8), (0, 0), (h - 1, w - 1), (6, 0), (0, 8),
                       (h - 2, w - 2)):
            broken = [r[:] for r in raw]
            broken[by][bx] += 4000
            data = doc(pattern, broken, bad=[[by, bx]])
            out = run_pipeline(data)
            check(len(out["corrected"]) == 1,
                  "%s: expected 1 correction at (%d,%d), got %r"
                  % (pattern, by, bx, out["corrected"]))
            check(out["corrected"][0][:2] == [by, bx],
                  "%s: corrected position wrong" % pattern)
            check(out["corrected"][0][2] == broken[by][bx],
                  "%s: corrected record lost the original sample" % pattern)
            check(out["uncorrectable"] == [], "%s: uncorrectable non-empty"
                  % pattern)
            # The repaired value is the same-colour distance-2 median.
            ring = sorted(raw[ny][nx]
                          for ny in range(h) for nx in range(w)
                          if max(abs(ny - by), abs(nx - bx)) == 2
                          and ref_table(pattern)[ny & 1][nx & 1]
                          == ref_table(pattern)[by & 1][bx & 1])
            check(out["corrected"][0][3] == ring[len(ring) // 2],
                  "%s: repaired value %r not the ring median %r"
                  % (pattern, out["corrected"][0][3], ring[len(ring) // 2]))
            # A single bad pixel may only influence the pixel itself and the
            # neighbours that interpolate from it.
            clean = run_pipeline(doc(pattern, raw))
            for y in range(h):
                for x in range(w):
                    if max(abs(y - by), abs(x - bx)) <= 1:
                        continue
                    check(out["rgb"][y * w + x] == clean["rgb"][y * w + x],
                          "%s: bad pixel at (%d,%d) leaked to (%d,%d)"
                          % (pattern, by, bx, y, x))
            # All normal (unmarked) own-channel samples stay untouched, i.e.
            # the demosaic owns the same raw value at every site.
            diag = {}
            bayer.run(data, diag)
            for y in range(h):
                for x in range(w):
                    own = ref_table(pattern)[y & 1][x & 1]
                    want = out["corrected"][0][3] if (y, x) == (by, bx) \
                        else raw[y][x]
                    got = diag["demosaic_sensor"][y][x]["RGB".index(own)]
                    check(abs(got - want) < 1e-9,
                          "%s: sample at (%d,%d) is %r, expected %r"
                          % (pattern, y, x, got, want))


@test("single bad pixel value equals median of the distance-2 ring")
def t_single_bad_median():
    w, h = 9, 9
    raw = [[100 for _ in range(w)] for _ in range(h)]
    ring = {(-2, 0): 150, (2, 0): 160, (0, -2): 170, (0, 2): 180,
            (-2, -2): 190, (-2, 2): 200, (2, -2): 210, (2, 2): 220}
    for pattern in PATTERNS:
        tab = ref_table(pattern)
        for (dy, dx), v in ring.items():
            y, x = 4 + dy, 4 + dx
            if tab[y & 1][x & 1] == tab[4 & 1][4 & 1]:
                raw[y][x] = v
        broken = [r[:] for r in raw]
        broken[4][4] = 9000
        out = run_pipeline(doc(pattern, broken, bad=[[4, 4]]))
        want = sorted(ring.values())[4]  # 8 values -> upper median index 4
        check(out["corrected"][0][3] == want,
              "%s: median %r != %r" % (pattern, out["corrected"][0][3], want))


@test("dense bad pixels on a hard edge: same-colour median, order aware")
def t_dense_edge():
    w, h = 40, 30
    truth = edge_scene(w, h)
    # A thick band straddling the edge, including pixels 2 apart vertically
    # so corrections see already-corrected same-colour samples.
    bad = [[y, x] for y in range(9, 21) for x in range(17, 23)]
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        broken = [r[:] for r in raw]
        for by, bx in bad:
            broken[by][bx] = 4095 if (by + bx) % 2 else 0
        data = doc(pattern, broken, bad=bad)
        got = run_pipeline(data)
        want = ref_run(data)
        compare_output(attach_dims(got, w, h), attach_dims(want, w, h),
                       "dense %s" % pattern)
        check(len(got["corrected"]) == len(bad),
              "%s: %d corrections for %d bad pixels"
              % (pattern, len(got["corrected"]), len(bad)))


@test("dense edge bad pixels do not pollute pixels outside the marked set")
def t_dense_isolation():
    w, h = 40, 30
    truth = ramp_scene(w, h)
    bad = [[y, x] for y in range(6, 16) for x in (13, 15, 17)]
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        broken = [r[:] for r in raw]
        for by, bx in bad:
            broken[by][bx] = 0
        out = bayer.run(doc(pattern, broken, bad=bad))
        # Every unmarked pixel's own channel must still be its raw value.
        for y in range(h):
            for x in range(w):
                if [y, x] in bad:
                    continue
                own = ref_table(pattern)[y & 1][x & 1]
                check(out["rgb"] is not None, "no output")
        # Corrected values must sit inside the scene's value range.
        for _, _, old, new in out["corrected"]:
            check(0 <= new <= 255, "%s: correction out of range: %r"
                  % (pattern, new))


@test("odd crop origin: cropped frame equals crop of the full frame")
def t_odd_origin():
    w, h = 40, 32
    for pattern in PATTERNS:
        truth = ramp_scene(w, h)
        full_raw = mosaic(truth, pattern)
        full = bayer.run(doc(pattern, full_raw))
        for y0, x0 in ((1, 1), (1, 0), (0, 1), (3, 5)):
            hh, ww = h - y0 - 1, w - x0 - 1
            sub_raw = crop(full_raw, y0, x0, hh, ww)
            phase = crop_phase(pattern, y0, x0)
            sub = bayer.run(doc(phase, sub_raw))
            for y in range(1, hh - 1):
                for x in range(1, ww - 1):
                    got = sub["rgb"][y * ww + x]
                    want = full["rgb"][(y + y0) * w + (x + x0)]
                    if got != want:
                        raise Failure(
                            "%s crop(%d,%d) pixel (%d,%d): %r != %r"
                            % (pattern, y0, x0, y, x, got, want))


@test("odd crop origin matches reference on a defect-laden frame")
def t_odd_origin_reference():
    w, h = 30, 26
    truth = edge_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        for y0, x0 in ((1, 1), (2, 3)):
            hh, ww = h - y0, w - x0
            sub = crop(raw, y0, x0, hh, ww)
            phase = crop_phase(pattern, y0, x0)
            bad = [[2, 2], [2, 3], [4, ww // 2], [hh - 3, ww - 3]]
            data = doc(phase, sub, bad=bad)
            got = run_pipeline(data)
            want = ref_run(data)
            compare_output(attach_dims(got, ww, hh), attach_dims(want, ww, hh),
                           "%s crop(%d,%d)" % (pattern, y0, x0))


@test("explicit crop_origin field handles odd origins")
def t_origin_field():
    w, h = 36, 28
    truth = ramp_scene(w, h)
    for pattern in PATTERNS:
        full_raw = mosaic(truth, pattern)
        full = bayer.run(doc(pattern, full_raw))
        for y0, x0 in ((1, 1), (1, 0), (0, 1), (2, 5)):
            hh, ww = h - y0, w - x0
            sub_raw = crop(full_raw, y0, x0, hh, ww)
            # The stored samples still carry the sensor CFA phase: the origin
            # field must rotate the phase table so this frame is identical to
            # the same crop described by its own shifted pattern string.
            data = doc(pattern, sub_raw)
            data["crop_origin"] = [y0, x0]
            via_origin = bayer.run(data)

            phase = crop_phase(pattern, y0, x0)
            via_phase = bayer.run(doc(phase, sub_raw))
            check(via_origin["rgb"] == via_phase["rgb"],
                  "%s origin field (%d,%d) differs from shifted pattern"
                  % (pattern, y0, x0))
            for y in range(1, hh - 1):
                for x in range(1, ww - 1):
                    got = via_origin["rgb"][y * ww + x]
                    want = full["rgb"][(y + y0) * w + (x + x0)]
                    check(got == want,
                          "%s origin (%d,%d) pixel (%d,%d): %r != %r"
                          % (pattern, y0, x0, y, x, got, want))
    # Even origins are a no-op and need no special casing.
    raw = mosaic(ramp_scene(16, 12), "GBRG")
    plain = bayer.run(doc("GBRG", raw))
    shifted = doc("GBRG", raw)
    shifted["crop_origin"] = [2, 4]
    check(bayer.run(shifted)["rgb"] == plain["rgb"],
          "even crop origin changed the output")


@test("origin_y/origin_x split fields are accepted")
def t_origin_split():
    raw = mosaic(ramp_scene(20, 16), "RGGB")
    a = doc("RGGB", raw)
    a["origin_y"], a["origin_x"] = 1, 1
    b = doc("RGGB", raw)
    b["crop_origin"] = [1, 1]
    check(bayer.run(a)["rgb"] == bayer.run(b)["rgb"],
          "split origin fields disagree with crop_origin")


@test("hue is pattern-independent (no green shift from a hardcoded RGGB)")
def t_no_green_shift():
    w, h = 24, 18
    for truth_colour in ((0, 200, 0), (200, 0, 0), (0, 0, 200),
                         (200, 120, 40), (40, 120, 200)):
        truth = [[list(truth_colour) for _ in range(w)] for _ in range(h)]
        outputs = []
        for pattern in PATTERNS:
            raw = mosaic(truth, pattern)
            out = bayer.run(doc(pattern, raw))
            for i, px in enumerate(out["rgb"]):
                check(px == list(truth_colour),
                      "%s: flat %r scene gave %r at pixel %d"
                      % (pattern, truth_colour, px, i))
            outputs.append(out["rgb"])
        for other in outputs[1:]:
            check(other == outputs[0],
                  "output differs between patterns for %r" % (truth_colour,))


@test("green-only scene has no residual colour offset across patterns")
def t_green_balance():
    w, h = 16, 16
    # Slightly textured green so interpolation actually runs.
    truth = [[[40, 150 + (x % 3), 40] for x in range(w)] for _ in range(h)]
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        out = bayer.run(doc(pattern, raw))
        for y in range(2, h - 2):
            for x in range(2, w - 2):
                r, g, b = out["rgb"][y * w + x]
                check(r == 40 and b == 40,
                      "%s: green scene leaked into R/B at (%d,%d): %r"
                      % (pattern, y, x, out["rgb"][y * w + x]))
                check(abs(g - truth[y][x][1]) <= 1,
                      "%s: green level drifted at (%d,%d)" % (pattern, y, x))


@test("per-channel black level: mid-grey scene normalises uniformly")
def t_per_channel_black():
    w, h = 20, 16
    # Ground truth mid-grey everywhere; the sensor offsets each channel.
    black = {"R": 100, "G": 160, "B": 40}
    white = {"R": 355, "G": 415, "B": 295}  # span 255 for every channel
    sensor = [100 + black[c] for c in "RGB"]
    truth = [[[100, 100, 100] for _ in range(w)] for _ in range(h)]
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        tab = ref_table(pattern)
        for y in range(h):
            for x in range(w):
                raw[y][x] += black[tab[y & 1][x & 1]]
        check(raw[0][0] == 100 + black[tab[0][0]], "scene build error")
        out = bayer.run(doc(pattern, raw, black=black, white=white))
        for i, px in enumerate(out["rgb"]):
            check(px == [100, 100, 100],
                  "%s: pixel %d normalised to %r (black level not honoured)"
                  % (pattern, i, px))
    check(len(sensor) == 3, "unused")

    # The conventional 4-value per-site form (R, Gr, Gb, B) is also accepted:
    # here both greens are offset equally so the result matches the 3-value
    # form above.
    black4 = {"R": 100, "Gr": 160, "Gb": 160, "B": 40}
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        tab = ref_table(pattern)
        for y in range(h):
            for x in range(w):
                raw[y][x] += black[tab[y & 1][x & 1]]
        out = bayer.run(doc(pattern, raw, black=black4, white=white))
        check(all(px == [100, 100, 100] for px in out["rgb"]),
              "%s: 4-value black level not honoured" % pattern)


@test("scalar black level subtracts before scaling")
def t_scalar_black():
    w, h = 12, 10
    raw = [[300 for _ in range(w)] for _ in range(h)]
    out = bayer.run(doc("RGGB", raw, black=200, white=455))
    check(all(px == [100, 100, 100] for px in out["rgb"]),
          "scalar black level mishandled: %r" % out["rgb"][:3])
    # Identical scene without offset must give the same picture.
    out2 = bayer.run(doc("RGGB", [[100] * w for _ in range(h)],
                         black=0, white=255))
    check(all(px == [100, 100, 100] for px in out2["rgb"]),
          "offset-free scene scaled wrongly")


@test("black level list form [r,g,b] is accepted")
def t_black_list():
    w, h = 8, 8
    raw = [[150 for _ in range(w)] for _ in range(h)]
    out = bayer.run(doc("BGGR", raw, black=[50, 100, 0], white=255))
    # R: (150-50)*255/205 -> 124, G: (150-100)*255/155 -> 82, B: 150.
    check(all(px == [124, 82, 150] for px in out["rgb"]),
          "list black level mishandled: %r" % out["rgb"][:2])
    # Per-channel white levels span 100 for every channel.
    out2 = bayer.run(doc("BGGR", raw, black=[50, 100, 0],
                         white=[150, 200, 100]))
    check(all(px == [255, 128, 255] for px in out2["rgb"]),
          "list white level mishandled: %r" % out2["rgb"][:2])


@test("saturated and under-black samples clamp per channel")
def t_clamp():
    w, h = 8, 8
    raw = [[1000 for _ in range(w)] for _ in range(h)]
    out = bayer.run(doc("RGGB", raw, black=100, white=900))
    check(all(px == [255, 255, 255] for px in out["rgb"]),
          "white clamp broken")
    out = bayer.run(doc("RGGB", [[0] * w for _ in range(h)],
                        black=100, white=900))
    check(all(px == [0, 0, 0] for px in out["rgb"]), "black clamp broken")


@test("uncorrectable bad pixel keeps raw sample and is reported")
def t_uncorrectable():
    for pattern in PATTERNS:
        raw = [[500, 501], [502, 503]]
        data = doc(pattern, raw, bad=[[0, 0], [1, 1]])
        diag = {}
        out = bayer.run(data, diag)
        check(out["corrected"] == [],
              "%s: unexpectedly corrected %r" % (pattern, out["corrected"]))
        check(out["uncorrectable"] == [[0, 0], [1, 1]],
              "%s: uncorrectable list %r" % (pattern, out["uncorrectable"]))
        # The raw diagnostic frame must still hold the unprocessed samples...
        check(diag["raw_corrected"] == raw,
              "%s: uncorrectable sample was modified" % pattern)
        check(diag["uncorrectable_mask"][0][0] == 1
              and diag["uncorrectable_mask"][1][1] == 1,
              "%s: defect map lost the uncorrectable sites" % pattern)
        # ...and the pipeline must not invent a value: the sample is merely
        # clamped by the normalisation stage, exactly like any raw sample.
        for (y, x) in ((0, 0), (1, 1)):
            own = ref_table(pattern)[y & 1][x & 1]
            idx = "RGB".index(own)
            check(abs(diag["demosaic_sensor"][y][x][idx] - raw[y][x]) < 1e-9,
                  "%s: own channel at (%d,%d) lost its raw sample"
                  % (pattern, y, x))
            check(out["rgb"][y * 2 + x][idx] == 255,
                  "%s: saturation of the retained sample mishandled" % pattern)


@test("fewer than 3 same-colour neighbours averages instead of median")
def t_average_fallback():
    # 3x3 image: pixel (1,1) has only (±2) neighbours outside -> uncorrectable.
    # Use a 5x3 image so exactly two same-colour neighbours exist.
    w, h = 3, 5
    for pattern in PATTERNS:
        raw = [[200 for _ in range(w)] for _ in range(h)]
        tab = ref_table(pattern)
        same = [(y, x) for y in range(h) for x in range(w)
                if (y, x) != (0, 0) and tab[y & 1][x & 1] == tab[0][0]]
        # (0,0) in a 3-wide, 5-high frame: neighbours (0,2),(2,0),(2,2) exist.
        raw[0][2] = 300
        raw[2][0] = 100
        raw[2][2] = 200
        for y in range(h):
            for x in range(w):
                if (y, x) not in ((0, 2), (2, 0), (2, 2)):
                    raw[y][x] = 200
        broken = [r[:] for r in raw]
        broken[0][0] = 5000
        out = bayer.run(doc(pattern, broken, bad=[[0, 0]]))
        check(out["corrected"][0][3] == 200,
              "%s: median of (300,100,200) should be 200, got %r"
              % (pattern, out["corrected"]))

    # 1-pixel-wide frame: no same-colour neighbour at distance 2 in x.
    for pattern in PATTERNS:
        raw = [[pow(2, y) for _ in range(1)] for y in range(5)]
        out = bayer.run(doc(pattern, raw, bad=[[0, 0]]))
        # Only (2,0) and (4,0) share the colour of (0,0) in a 1-px column.
        check(len(out["uncorrectable"]) + len(out["corrected"]) == 1,
              "%s: bad pixel neither corrected nor reported" % pattern)


@test("bad_pixels input order is preserved in the corrected list")
def t_order():
    w, h = 12, 12
    raw = [[100 for _ in range(w)] for _ in range(h)]
    bad = [[6, 6], [2, 2], [8, 8], [4, 4]]
    out = bayer.run(doc("GBRG", raw, bad=bad))
    check([c[:2] for c in out["corrected"]] == bad,
          "correction order changed: %r" % out["corrected"])


@test("duplicate/out-of-range bad-pixel entries are handled safely")
def t_robust_input():
    w, h = 10, 10
    raw = [[100 for _ in range(w)] for _ in range(h)]
    out = bayer.run(doc("BGGR", raw, bad=[[3, 3], [3, 3], [-1, 2], [10, 10],
                                          [2, 2]]))
    check([c[:2] for c in out["corrected"]] == [[3, 3], [2, 2]],
          "duplicates/out-of-range mishandled: %r" % out["corrected"])
    check(out["uncorrectable"] == [], "unexpected uncorrectable entries")


@test("unknown CFA pattern is rejected")
def t_bad_pattern():
    try:
        bayer.pattern_table("RGBG")
    except ValueError:
        pass
    else:
        raise Failure("invalid pattern accepted")


@test("diagnostic map layout is consistent with the frame")
def t_diag():
    w, h = 15, 11
    truth = bars_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        diag = {}
        bayer.run(doc(pattern, raw, bad=[[5, 5]]), diag)
        for key in ("raw_original", "raw_corrected", "bad_mask",
                    "corrected_mask", "uncorrectable_mask", "demosaic_sensor"):
            check(len(diag[key]) == h and len(diag[key][0]) == w,
                  "%s: %s has wrong shape" % (pattern, key))
        check(diag["raw_original"][5][5] == raw[5][5],
              "raw_original is not the unprocessed frame")
        check(diag["corrected_mask"][5][5] == 1, "corrected mask empty")
        check(diag["bad_mask"][5][5] == 1, "bad mask empty")
        ring = sorted(raw[ny][nx] for ny in range(h) for nx in range(w)
                      if max(abs(ny - 5), abs(nx - 5)) == 2
                      and ref_table(pattern)[ny & 1][nx & 1]
                      == ref_table(pattern)[5 & 1][5 & 1])
        check(diag["raw_corrected"][5][5] == ring[len(ring) // 2],
              "%s: raw_corrected was not repaired (%r, ring median %r)"
              % (pattern, diag["raw_corrected"][5][5], ring[len(ring) // 2]))
        # Processed output only; no diagnostic fields leak into the result.
        out = bayer.run(doc(pattern, raw, bad=[[5, 5]]))
        check(set(out) == {"rgb", "corrected", "uncorrectable"},
              "output schema changed: %r" % sorted(out))


@test("CLI writes the same document as the library entry point")
def t_cli():
    w, h = 21, 17
    truth = bars_scene(w, h)
    for pattern in PATTERNS:
        raw = mosaic(truth, pattern)
        data = doc(pattern, raw, bad=[[4, 4], [9, 15]], black=8, white=250)
        with tempfile.TemporaryDirectory() as td:
            ip = os.path.join(td, "in.json")
            op = os.path.join(td, "out.json")
            dp = os.path.join(td, "diag.json")
            with open(ip, "w") as fh:
                json.dump(data, fh)
            subprocess.check_call(
                [sys.executable, os.path.join(HERE, "bayer.py"), ip, op,
                 "--diag", dp], cwd=HERE)
            with open(op) as fh:
                got = json.load(fh)
            with open(dp) as fh:
                diag = json.load(fh)
        want = bayer.run(data)
        check(sorted(got) == sorted(want), "CLI emitted %r" % sorted(got))
        check(got["rgb"] == want["rgb"], "CLI rgb differs")
        check(got["corrected"] == want["corrected"], "CLI corrected differs")
        check(len(diag["bad_mask"]) == h, "CLI diagnostic wrong shape")


@test("legacy call signature (2 args) still works")
def t_legacy_main():
    with tempfile.TemporaryDirectory() as td:
        ip = os.path.join(td, "in.json")
        op = os.path.join(td, "out.json")
        with open(ip, "w") as fh:
            json.dump(doc("RGGB", [[10, 20], [30, 40]]), fh)
        subprocess.check_call([sys.executable,
                               os.path.join(HERE, "bayer.py"), ip, op],
                              cwd=HERE)
        with open(op) as fh:
            got = json.load(fh)
        check(len(got["rgb"]) == 4, "legacy invocation produced %r"
              % sorted(got))


@test("all scene/pattern/origin combinations agree with reference")
def t_matrix():
    scenes = {
        "bars": bars_scene(32, 24),
        "edge": edge_scene(32, 24),
        "ramp": ramp_scene(32, 24),
    }
    for name, truth in scenes.items():
        for pattern in PATTERNS:
            raw = mosaic(truth, pattern)
            for y0, x0 in ((0, 0), (1, 1), (1, 2), (3, 1)):
                hh, ww = 24 - y0, 32 - x0
                sub = crop(raw, y0, x0, hh, ww)
                phase = crop_phase(pattern, y0, x0)
                bad = [[4, 5], [hh - 5, ww - 6], [7, ww // 2]]
                data = doc(phase, sub, bad=bad, black=4, white=250)
                got = run_pipeline(data)
                want = ref_run(data)
                compare_output(attach_dims(got, ww, hh),
                               attach_dims(want, ww, hh),
                               "%s/%s/origin(%d,%d)" % (name, pattern, y0, x0))


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
