"""Independent floating-point reference pipeline.

This module deliberately does NOT import convert.py.  Every stage is
re-derived from the specification with different code structure (scalar
per-pixel processing, no precomputed tables, different summation order), so
agreement within one code value validates the production implementation
rather than echoing it.
"""
import math

# --- coefficients re-derived from Kr/Kb rather than copied constants --------
KR_20, KB_20 = 0.2627, 0.0593
KG_20 = 1.0 - KR_20 - KB_20
KR_7, KB_7 = 0.2126, 0.0722
KG_7 = 1.0 - KR_7 - KB_7

GAMUT = (
    (1.660491, -0.587641, -0.072850),
    (-0.124550, 1.132900, -0.008349),
    (-0.018151, -0.100579, 1.118730),
)
M1 = 2610.0 / 16384.0
M2 = 2523.0 / 4096.0 * 128.0
C1 = 3424.0 / 4096.0
C2 = 2413.0 / 4096.0 * 32.0
C3 = 2392.0 / 4096.0 * 32.0


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def pq_decode(e):
    """PQ signal -> nits, written independently from the production copy."""
    if e <= 0.0:
        return 0.0
    root = e ** (1.0 / M2)
    num = root - C1
    if num <= 0.0:
        return 0.0
    den = C2 - C3 * root
    if den <= 0.0:
        return 10000.0
    base = num / den
    return 10000.0 * base ** (1.0 / M1)


def oetf709(x):
    x = _clamp(x, 0.0, 1.0)
    if x < 0.018:
        return 4.5 * x
    return 1.099 * (x ** 0.45) - 0.099


def _norm(code, kind):
    if kind == "y-full":
        return code / 1023.0
    if kind == "c-full":
        return (code - 512.0) / 1023.0
    if kind == "y-lim":
        return (code - 64.0) / 876.0
    return (code - 512.0) / 896.0


def _sample_bilinear(plane, fx, fy):
    rows = len(plane)
    cols = len(plane[0])
    j = math.floor(fy)
    i = math.floor(fx)
    ty = fy - j
    tx = fx - i
    j0 = _clamp(j, 0, rows - 1)
    j1 = _clamp(j + 1, 0, rows - 1)
    i0 = _clamp(i, 0, cols - 1)
    i1 = _clamp(i + 1, 0, cols - 1)
    # Accumulate in a deliberately different order than the production code.
    acc = ty * tx * plane[j1][i1]
    acc += ty * (1.0 - tx) * plane[j1][i0]
    acc += (1.0 - ty) * tx * plane[j0][i1]
    acc += (1.0 - ty) * (1.0 - tx) * plane[j0][i0]
    return acc


def _chroma_position(x, y, siting):
    # Luma centres on integers; chroma sample i sits near luma column 2i.
    cy = y / 2.0 - 0.25
    if siting == "center":
        cx = x / 2.0 - 0.25
    else:  # left: co-sited horizontally
        cx = x / 2.0
    return cx, cy


def reference_pipeline(frame, white_nits, target_siting):
    """Return (y8, cb8, cr8, debug) computed fully independently."""
    w, h = frame["width"], frame["height"]
    meta = frame["metadata"]
    rng = meta["range"]
    siting = meta["siting"]
    cw, ch = (w + 1) // 2, (h + 1) // 2

    dbg2020 = [[[0.0] * 3 for _ in range(w)] for _ in range(h)]
    dbg709 = [[[0.0] * 3 for _ in range(w)] for _ in range(h)]
    dbgmap = [[[0.0] * 3 for _ in range(w)] for _ in range(h)]
    ysig = [[0.0] * w for _ in range(h)]
    cbsig = [[0.0] * w for _ in range(h)]
    crsig = [[0.0] * w for _ in range(h)]
    luma_grid = [[0.0] * w for _ in range(h)]

    # Pre-normalize every plane.
    yn = [[_norm(v, "y-lim" if rng == "limited" else "y-full")
           for v in row] for row in frame["y"]]
    ckind = "c-lim" if rng == "limited" else "c-full"
    cbn = [[_norm(v, ckind) for v in row] for row in frame["cb"]]
    crn = [[_norm(v, ckind) for v in row] for row in frame["cr"]]

    vr = 2.0 * (1.0 - KR_20)
    vb = 2.0 * (1.0 - KB_20)
    for y in range(h):
        for x in range(w):
            cx, cy = _chroma_position(x, y, siting)
            cbv = _sample_bilinear(cbn, cx, cy)
            crv = _sample_bilinear(crn, cx, cy)
            yv = yn[y][x]
            # Solve R' = Y' + Vr*Cr', etc., then clip each signal.
            rp = _clamp(yv + vr * crv, 0.0, 1.0)
            bp = _clamp(yv + vb * cbv, 0.0, 1.0)
            gp = _clamp(
                yv
                - (2.0 * KB_20 * (1.0 - KB_20) / KG_20) * cbv
                - (2.0 * KR_20 * (1.0 - KR_20) / KG_20) * crv,
                0.0, 1.0,
            )
            lin20 = (pq_decode(rp), pq_decode(gp), pq_decode(bp))
            dbg2020[y][x] = list(lin20)
            lin7 = [
                GAMUT[c][0] * lin20[0]
                + GAMUT[c][2] * lin20[2]
                + GAMUT[c][1] * lin20[1]
                for c in range(3)
            ]
            dbg709[y][x] = list(lin7)
            lum = (
                KB_7 * lin7[2]
                + KR_7 * lin7[0]
                + KG_7 * lin7[1]
            )
            luma_grid[y][x] = lum
            denom = white_nits + (lum if lum > 0.0 else 0.0)
            mapped = [_clamp(c / denom, 0.0, 1.0) for c in lin7]
            dbgmap[y][x] = list(mapped)
            sig = [oetf709(c) for c in mapped]
            y_enc = (
                KB_7 * sig[2] + KR_7 * sig[0] + KG_7 * sig[1]
            )
            ysig[y][x] = y_enc
            cbsig[y][x] = (sig[2] - y_enc) / (2.0 * (1.0 - KB_7))
            crsig[y][x] = (sig[0] - y_enc) / (2.0 * (1.0 - KR_7))

    # Chroma downsampling by explicit support enumeration.
    cb8 = [[0] * cw for _ in range(ch)]
    cr8 = [[0] * cw for _ in range(ch)]
    for j in range(ch):
        ysupp = [yy for yy in (2 * j, 2 * j + 1) if yy < h]
        for i in range(cw):
            if target_siting == "left":
                xsupp = [2 * i]
            else:
                xsupp = [xx for xx in (2 * i, 2 * i + 1) if xx < w]
            n = len(xsupp) * len(ysupp)
            for grid, out in ((cbsig, cb8), (crsig, cr8)):
                acc = 0.0
                for yy in ysupp:
                    for xx in xsupp:
                        acc += grid[yy][xx]
                out[j][i] = acc / n

    y8 = [
        [
            _clamp(int(math.floor(16.0 + 219.0 * ysig[y][x] + 0.5)), 16, 235)
            for x in range(w)
        ]
        for y in range(h)
    ]
    for out in (cb8, cr8):
        for j in range(ch):
            for i in range(cw):
                out[j][i] = _clamp(
                    int(math.floor(128.0 + 224.0 * out[j][i] + 0.5)),
                    16, 240,
                )
    return y8, cb8, cr8, {
        "rgb2020_nits": dbg2020,
        "rgb709_nits": dbg709,
        "mapped_linear709": dbgmap,
    }, luma_grid


def reference_luma_grid(frame):
    """Only the linear BT.709 luma per pixel (for offline shot truth)."""
    return reference_pipeline(frame, 203.0, frame["metadata"]["siting"])[4]


def reference_shot_stats(entries):
    """Offline sorted-truth P95 and white parameter for one shot."""
    weighted = []
    pixel_ticks = 0
    for entry in entries:
        frame = entry["frame"]
        w, h = frame["width"], frame["height"]
        lum = reference_luma_grid(frame)
        dur = entry["duration"]
        pixel_ticks += dur * w * h
        for row in lum:
            for value in row:
                b = int(math.floor(value + 0.5))
                b = _clamp(b, 0, 10000)
                weighted.append((b, dur))
    weighted.sort(key=lambda item: item[0])
    target_num = pixel_ticks * 95
    acc = 0
    p95 = 10000
    for nit, weight in weighted:
        acc += weight
        if acc * 100 >= target_num:
            p95 = nit
            break
    half = p95 / 2.0
    white = 203.0 if half <= 203.0 else half
    return pixel_ticks, p95, white
