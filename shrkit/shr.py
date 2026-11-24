"""Apple IIgs super hi-res screens: 200 lines, per-line SCBs, 16 palettes."""
import struct

POINTS = {"top_left": (0, 0), "x1": (1, 0), "x2": (2, 0), "x3": (3, 0), "row_1": (0, 1), "center": (320, 100),
          "bottom_right": (639, 199)}


class ShrError(ValueError):
    pass


def rgb(word):
    return "#%02x%02x%02x" % ((word >> 8 & 15) * 17, (word >> 4 & 15) * 17, (word & 15) * 17)


def parse(data):
    if len(data) != 32768:
        raise ShrError("not a super hi-res screen (%d bytes)" % len(data))
    pals = [struct.unpack_from(">16H", data, 0x7E00 + 32 * p) for p in range(16)]
    scbs = data[0x7D00:0x7DC8]
    lines = []
    for y in range(200):
        pal = pals[scbs[y] & 7]
        out = []
        for b in data[y * 160:(y + 1) * 160]:
            for v in (b & 15, b >> 4):
                out += [pal[v], pal[v]]
        lines.append(out)
    return {"modes": {"320": sum(1 for s in scbs if not s & 0x80), "640": sum(1 for s in scbs if s & 0x80)},
            "interrupt_lines": sum(1 for s in scbs if s & 0x40),
            "palettes_used": sorted({s & 15 for s in scbs}),
            "points": {k: rgb(lines[y][x]) for k, (x, y) in POINTS.items()},
            "colors": len({c & 0xFFF for ln in lines for c in ln})}
