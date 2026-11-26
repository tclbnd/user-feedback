"""PKM textures (.pkm) with ETC1 and ETC2 RGB blocks."""
import struct

TABLES = [(2, 8), (5, 17), (9, 29), (13, 42), (18, 60), (24, 80), (33, 106), (47, 183)]
KINDS = {0: "ETC1", 1: "ETC2"}


class PkmError(ValueError):
    pass


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _c(v):
    return max(0, min(255, v))


def _e4(v):
    return v << 4 | v


def _e5(v):
    return v << 3 | v >> 2


def _index(v, x, y):
    i = y * 4 + x
    return (v >> (16 + i) & 1) << 1 | (v >> i & 1)


def block(q, etc2):
    """Return (mode, 4x4 list of (r, g, b) indexed [y][x])."""
    v = struct.unpack(">Q", q)[0]
    hi = v >> 32
    diff, flip = hi >> 1 & 1, hi & 1
    out = [[None] * 4 for _ in range(4)]
    if diff:
        r, g, b = hi >> 27 & 31, hi >> 19 & 31, hi >> 11 & 31
        dr, dg, db = hi >> 24 & 7, hi >> 16 & 7, hi >> 8 & 7
        b1 = (_e5(r), _e5(g), _e5(b))
        b2 = (_e5(min(r + dr, 31)), _e5(min(g + dg, 31)), _e5(min(b + db, 31)))
        mode = "differential"
    else:
        b1 = (_e4(hi >> 28 & 15), _e4(hi >> 20 & 15), _e4(hi >> 12 & 15))
        b2 = (_e4(hi >> 24 & 15), _e4(hi >> 16 & 15), _e4(hi >> 8 & 15))
        mode = "individual"
    t1, t2 = TABLES[hi >> 5 & 7], TABLES[hi >> 2 & 7]
    for y in range(4):
        for x in range(4):
            second = (y >= 2) if flip else (x >= 2)
            base, (a, bb) = (b2, t2) if second else (b1, t1)
            m = (-bb, -a, a, bb)[_index(v, x, y)]
            out[y][x] = tuple(_c(c + m) for c in base)
    return mode, out


def parse(data):
    if len(data) < 16 or data[:4] != b"PKM " or data[4:6] not in (b"10", b"20"):
        raise PkmError("not a PKM texture")
    kind, ew, eh, w, h = struct.unpack_from(">HHHHH", data, 6)
    if data[4:6] == b"10":
        kind = 0
    if kind not in KINDS:
        raise PkmError("unsupported PKM data type %d" % kind)
    bw, bh = ew // 4, eh // 4
    body = data[16:]
    out = {"format": KINDS[kind], "width": ew, "height": eh, "blocks": bw * bh, "complete": len(body) >= 8 * bw * bh}
    if not out["complete"] or w == 0 or h == 0:
        return {**out, "modes": None, "colors_used": None, "first_row": None, "last_row": None}
    modes = {"individual": 0, "differential": 0, "T": 0, "H": 0, "planar": 0}
    img = [[None] * (bw * 4) for _ in range(bh * 4)]
    for by in range(bh):
        for bx in range(bw):
            mode, px = block(body[8 * (by * bw + bx):8 * (by * bw + bx) + 8], kind == 1)
            modes[mode] += 1
            for y in range(4):
                for x in range(4):
                    img[by * 4 + y][bx * 4 + x] = px[y][x]
    hexrow = lambda row: ["#%02x%02x%02x" % c for c in row]
    seen = {c for row in img for c in row}
    return {**out, "modes": modes, "colors_used": len(seen), "first_row": _runs(hexrow(img[0])), "last_row": _runs(hexrow(img[-1]))}
