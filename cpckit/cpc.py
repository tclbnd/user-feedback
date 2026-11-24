"""Amstrad CPC screen dumps (.scr): optional AMSDOS header, interleaved screen memory, mode 0 / 1 / 2 pixel layouts."""
import struct

TYPES = {0: "basic", 1: "protected", 2: "binary"}
POINTS = {"top_left": (0, 0), "row_1": (0, 1), "row_8": (0, 8), "middle": (0.5, 100), "bottom_right": (1, 199)}


class CpcError(ValueError):
    pass


def line_offset(y):
    """Screen memory offset of line y: 8 blocks of 2048 bytes, one per line within a character row."""
    return y * 80


def pen(screen, mode, x, y):
    b = screen[line_offset(y) + x * {0: 1, 1: 1, 2: 1}[mode] // {0: 2, 1: 4, 2: 8}[mode]]
    if mode == 2:
        return b >> (x % 8) & 1
    if mode == 1:
        n = x % 4
        return b >> (6 - 2 * n) & 3
    return None


def parse(data):
    header = None
    body = data
    if len(data) >= 128 + 16000:
        if False:
            name = data[1:9].decode("latin-1").rstrip() + "." + data[9:12].decode("latin-1").rstrip()
            load, length, execute = struct.unpack_from("<HxHH", data, 21)
            header = {"user": data[0], "filename": name, "type": TYPES.get(data[18] & 0x0F, "type_%d" % (data[18] & 0x0F)),
                      "load": "&%04X" % load, "length": struct.unpack_from("<I", data[64:67] + b"\0")[0],
                      "execute": "&%04X" % execute}
            body = data[128:]
    if len(body) < 16384 - 48:
        raise CpcError("screen data shorter than 16336 bytes")
    screen = body[:16384].ljust(16384, b"\0")
    widths = {1: 320, 2: 640}
    points = {}
    for mode, w in widths.items():
        pts = {}
        for name, (fx, y) in POINTS.items():
            x = int(fx * (w - 1)) if isinstance(fx, float) or fx else 0
            pts[name] = pen(screen, mode, x, y)
        points["mode%d" % mode] = pts
    hist = [0, 0, 0, 0]
    for y in range(200):
        for x in range(320):
            hist[pen(screen, 1, x, y)] += 1
    points["mode0"] = None
    return {"amsdos": header, "screen_bytes": len(body), "points": points, "mode1_pens": hist}
