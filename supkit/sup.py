"""Blu-ray PGS subtitle streams (.sup): segments, display sets, objects, palettes, timing."""
import struct
from fractions import Fraction

PDS, ODS, PCS, WDS, END = 0x14, 0x15, 0x16, 0x17, 0x80


class SupError(ValueError):
    pass


def seconds(pts):
    """Presentation timestamps tick at 90 kHz."""
    return str(Fraction(pts, 90000))


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
    objects, fragments = {}, {}
    palettes = {}
    windows = []
    events = []
    out = {"width": None, "height": None, "display_sets": 0, "epochs": 0}
    pcs = None
    current = None
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
                oid, wid, flags, x, y = struct.unpack_from(">HBBHH", body, q)
                q += 8
                crop = None
                if flags & 0x80:                # object_cropped_flag: cropping parameters follow
                    cx, cy, cw, ch = struct.unpack_from(">HHHH", body, q)
                    q += 8
                    crop = {"x": cx, "y": cy, "width": cw, "height": ch}
                comps.append({"id": oid, "window": wid, "forced": bool(flags & 0x40),
                              "x": x, "y": y, "crop": crop})
            pcs = {"pts": pts, "pal_id": pal_id, "palette_update": bool(pal_update & 0x80), "comps": comps}
            out["display_sets"] += 1
        elif kind == WDS:
            windows = []
            for q in range(1, 1 + 9 * body[0], 9):
                wid, x, y, w, h = struct.unpack_from(">BHHHH", body, q)
                windows.append({"id": wid, "x": x, "y": y, "width": w, "height": h})
        elif kind == PDS:
            entries = palettes.setdefault(body[0], {})
            for q in range(2, len(body) - 4, 5):
                entries[body[q]] = body[q + 4]     # entry index -> alpha
        elif kind == ODS:
            oid, _version, seq = struct.unpack_from(">HBB", body)
            if oid not in fragments:
                # first (or only) fragment: 3-byte data length, then width and height
                length = int.from_bytes(body[4:7], "big") - 4
                w, h = struct.unpack_from(">HH", body, 7)
                fragments[oid] = {"width": w, "height": h, "length": length,
                                  "data": bytearray(body[11:])}
            else:                               # continuation fragment: bitmap data only
                fragments[oid]["data"] += body[3:]
            frag = fragments[oid]
            if len(frag["data"]) >= frag["length"]:
                objects[oid] = {"width": frag["width"], "height": frag["height"],
                                "data": bytes(frag["data"])}
                del fragments[oid]
        elif kind == END and pcs is not None:
            if pcs["palette_update"]:
                # palette-only display set (fade): the subtitle stays on screen
                if current is not None:
                    current["palette_updates"] += 1
            elif pcs["comps"]:
                if current is not None:         # replaced by the new composition
                    current["end"] = seconds(pcs["pts"])
                alpha = dict(palettes.get(pcs["pal_id"], {}))
                objs = []
                for c in pcs["comps"]:
                    o = objects.get(c["id"])
                    if o is None:
                        raise SupError("composition uses undefined object %d" % c["id"])
                    objs.append({"id": c["id"], "x": c["x"], "y": c["y"],
                                 "width": o["width"], "height": o["height"],
                                 "opaque_pixels": rle_pixels(o["data"], o["width"], o["height"], alpha),
                                 "window": c["window"], "crop": c["crop"]})
                current = {"start": seconds(pcs["pts"]), "end": None,
                           "forced": any(c["forced"] for c in pcs["comps"]),
                           "objects": objs, "windows": [dict(w) for w in windows],
                           "palette_updates": 0}
                events.append(current)
            elif current is not None:           # empty composition clears the screen
                current["end"] = seconds(pcs["pts"])
                current = None
            pcs = None
    out["subtitles"] = events
    return out
