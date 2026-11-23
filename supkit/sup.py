"""Blu-ray PGS subtitle streams (.sup): segments, display sets, objects, palettes, timing."""
import struct
from fractions import Fraction

PDS, ODS, PCS, WDS, END = 0x14, 0x15, 0x16, 0x17, 0x80


class SupError(ValueError):
    pass


def seconds(pts):
    return str(Fraction(pts, 1000))


def rle_pixels(data, width, height, alpha):
    """Decode the run-length coded bitmap and count pixels whose palette entry is not transparent."""
    p, n, opaque, pixels = 0, len(data), 0, 0

    def add(count, colour):
        nonlocal opaque, pixels
        pixels += count
        if alpha.get(colour, 0):
            opaque += count
    while p < n:
        b = data[p]
        p += 1
        if b:
            add(1, b)
            continue
        if p >= n:
            break
        f = data[p]
        p += 1
        if f == 0:                          # end of line
            continue
        kind, length = f >> 6, f & 0x3F
        if kind & 1:                        # 14-bit run length
            length = (length << 8) | data[p]
            p += 1
        colour = 0
        if kind & 2:
            colour = data[p]
            p += 1
        add(length, colour)
    return opaque


def parse(data):
    pos, n = 0, len(data)
    objects, palette = {}, {}
    events = []
    out = {"width": None, "height": None, "display_sets": 0, "epochs": 0}
    pcs = None
    while pos < n:
        if pos + 13 > n or data[pos:pos + 2] != b"PG":
            raise SupError("bad segment header at %d" % pos)
        pts, _dts, kind, size = struct.unpack_from(">IIBH", data, pos + 2)
        body = data[pos + 13:pos + 13 + size]
        pos += 13 + size
        if kind == PCS:
            w, h, _rate, number, state, pal_update, pal_id, count = struct.unpack_from(">HHBHBBBB", body)
            if out["width"] is None:
                out["width"], out["height"] = w, h
            if state & 0x80:
                out["epochs"] += 1
            comps, q = [], 11
            for _ in range(count):
                oid, _wid, flags, x, y = struct.unpack_from(">HBBHH", body, q)
                q += 8
                comps.append({"id": oid, "x": x, "y": y})
            pcs = {"pts": pts, "comps": comps}
            out["display_sets"] += 1
        elif kind == PDS:
            palette = {body[q]: body[q + 4] for q in range(2, len(body) - 4, 5)}
        elif kind == ODS:
            oid = struct.unpack_from(">H", body)[0]
            w, h = struct.unpack_from(">HH", body, 7)
            objects[oid] = {"width": w, "height": h, "data": body[11:]}
        elif kind == END and pcs is not None:
            objs = []
            for c in pcs["comps"]:
                o = objects.get(c["id"])
                if o is None:
                    raise SupError("composition uses undefined object %d" % c["id"])
                objs.append({"id": c["id"], "x": c["x"], "y": c["y"], "width": o["width"], "height": o["height"],
                             "opaque_pixels": rle_pixels(o["data"], o["width"], o["height"], palette)})
            events.append({"start": seconds(pcs["pts"]), "end": None, "forced": False, "objects": objs})
            pcs = None
    out["subtitles"] = events
    return out
