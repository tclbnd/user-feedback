"""Radiance pictures (.hdr, .pic): text header, resolution string, RGBE / XYZE scanlines."""
import math
import re


class HdrError(ValueError):
    pass


def _new_rle_scanline(data, p, n):
    """Decode one new-style (Greg Ward) run-length scanline of n pixels.

    It starts with 2, 2, width_hi, width_lo; each of the four components is
    encoded separately: a count byte c > 128 starts a run of (c - 128) copies
    of the next byte, otherwise c literal bytes follow.
    """
    width = data[p + 2] * 256 + data[p + 3]
    if width != n:
        raise HdrError("scanline width %d does not match picture width %d" % (width, n))
    p += 4
    chans = []
    for _ in range(4):
        out = bytearray()
        while len(out) < n:
            c = data[p]
            p += 1
            if c > 128:
                out += bytes([data[p]]) * (c - 128)
                p += 1
            else:
                out += data[p:p + c]
                p += c
        chans.append(out[:n])
    return [tuple(ch[i] for ch in chans) for i in range(n)], p, "rle"


def _old_scanline(data, p, n):
    """Decode one old-style scanline of n pixels.

    Pixels are four raw bytes each; a (1, 1, 1) pixel followed by a count byte
    repeats the previous pixel that many times (old RLE). Without any repeat
    code the scanline is simply flat data.
    """
    pixels = []
    kind = "flat"
    while len(pixels) < n:
        r, g, b, e = data[p], data[p + 1], data[p + 2], data[p + 3]
        p += 4
        if (r, g, b) == (1, 1, 1):
            if not pixels:
                raise HdrError("old-style repeat code before any pixel")
            pixels.extend([pixels[-1]] * e)
            kind = "old_rle"
        else:
            pixels.append((r, g, b, e))
    return pixels[:n], p, kind


def scanline(data, p, n):
    """Decode one scanline of n pixels starting at p; returns (pixels, new p, kind)."""
    # The 2, 2 marker is only valid for the new encoding, whose width must be
    # between 8 and 32767 and must match the picture width.
    if (p + 3 < len(data) and data[p] == 2 and data[p + 1] == 2
            and not (data[p + 2] & 0x80)
            and data[p + 2] * 256 + data[p + 3] == n):
        return _new_rle_scanline(data, p, n)
    return _old_scanline(data, p, n)


def value(m, e):
    """Convert one RGBE/XYZE mantissa/exponent pair the way Radiance does."""
    if e == 0:
        return 0.0
    return (m + 0.5) * math.ldexp(1.0, e - 136)


def parse(data):
    if not (data.startswith(b"#?RADIANCE\n") or data.startswith(b"#?RGBE\n")):
        raise HdrError("not a Radiance picture")
    p = data.index(b"\n") + 1
    fmt, exposure = None, 1.0
    while True:
        q = data.index(b"\n", p)
        line = data[p:q].decode("latin-1").strip()
        p = q + 1
        if not line:
            break
        if line.startswith("FORMAT="):
            fmt = line[7:].strip()
        elif line.startswith("EXPOSURE="):
            # Several EXPOSURE lines multiply together.
            exposure *= float(line[9:])
    q = data.index(b"\n", p)
    res = data[p:q].decode("latin-1")
    p = q + 1
    m = re.fullmatch(r"([-+])([XY])\s+(\d+)\s+([-+])([XY])\s+(\d+)", res.strip())
    if not m or m.group(2) == m.group(5):
        raise HdrError("bad resolution string %r" % res)
    s1, a1, n1, s2, a2, n2 = m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5), int(m.group(6))
    # The Y axis gives the number of scanlines and the X axis their length;
    # the signs say how file order maps onto the displayed picture.
    if a1 == "Y":
        y_sign, height, x_sign, width = s1, n1, s2, n2
    else:
        x_sign, width, y_sign, height = s1, n1, s2, n2
    grid = {}
    kinds = {}
    complete = True
    for i in range(height):
        try:
            px, p, kind = scanline(data, p, width)
        except (IndexError, ValueError):
            complete = False
            break
        kinds[kind] = kinds.get(kind, 0) + 1
        for j, c in enumerate(px):
            # Convert storage position (row i, column j) into displayed
            # coordinates (x, y), whose origin is the top-left corner.
            x = j if x_sign == "+" else width - 1 - j
            y = i if y_sign == "-" else height - 1 - i
            grid[(x, y)] = c

    def colour(x, y):
        if (x, y) not in grid:
            return None
        r, g, b, e = grid[(x, y)]
        return ["%.4f" % (value(v, e) / exposure) for v in (r, g, b)]

    pts = {"top_left": (0, 0), "top_right": (width - 1, 0), "bottom_left": (0, height - 1),
           "bottom_right": (width - 1, height - 1), "center": (width // 2, height // 2)}
    return {"format": fmt, "width": width, "height": height, "orientation": res.strip(),
            "exposure": "%.4g" % exposure, "scanlines": dict(sorted(kinds.items())), "complete": complete,
            "points": {k: colour(x, y) for k, (x, y) in pts.items()}}
