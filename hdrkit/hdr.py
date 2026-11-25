"""Radiance pictures (.hdr, .pic): text header, resolution string, RGBE / XYZE scanlines."""
import math
import re


class HdrError(ValueError):
    pass


def scanline(data, p, n):
    """Decode one scanline of n pixels starting at p; returns (pixels, new p, kind)."""
    if data[p:p + 2] != b"\x02\x02":
        raise HdrError("scanline is not run-length encoded")
    p += 4
    chans = []
    for _ in range(4):
        out = bytearray()
        while len(out) < n:
            c = data[p]
            p += 1
            if c > 128:
                out += bytes([data[p]]) * (c - 127)
                p += 1
            else:
                out += data[p:p + c]
                p += c
        chans.append(out[:n])
    return [tuple(ch[i] for ch in chans) for i in range(n)], p, "rle"


def value(m, e):
    return 0.0 if e == 0 else m * math.ldexp(1.0, e - 128)


def parse(data):
    if not (data.startswith(b"#?RADIANCE\n") or data.startswith(b"#?RGBE\n")):
        raise HdrError("not a Radiance picture")
    p = data.index(b"\n") + 1
    fmt, exposure = None, 1.0
    while True:
        q = data.index(b"\n", p)
        line = data[p:q].decode("latin-1")
        p = q + 1
        if not line:
            break
        if line.startswith("FORMAT="):
            fmt = line[7:].strip()
        elif line.startswith("EXPOSURE="):
            exposure = float(line[9:])
    q = data.index(b"\n", p)
    res = data[p:q].decode("latin-1")
    p = q + 1
    m = re.fullmatch(r"([-+])([XY]) (\d+) ([-+])([XY]) (\d+)", res.strip())
    if not m or m.group(2) == m.group(5):
        raise HdrError("bad resolution string %r" % res)
    s1, a1, n1, s2, a2, n2 = m.group(1), m.group(2), int(m.group(3)), m.group(4), m.group(5), int(m.group(6))
    width, height = n2, n1
    grid = {}
    kinds = {}
    complete = True
    for i in range(n1):
        try:
            px, p, kind = scanline(data, p, n2)
        except (IndexError, ValueError):
            complete = False
            break
        kinds[kind] = kinds.get(kind, 0) + 1
        for j, c in enumerate(px):
            grid[(j, i)] = c

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
