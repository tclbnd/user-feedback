"""Blu-ray PGS subtitle streams (.sup): segments, display sets, objects, palettes, timing."""
import struct
from fractions import Fraction

PDS, ODS, PCS, WDS, END = 0x14, 0x15, 0x16, 0x17, 0x80

# Object definition segment sequence flags.
ODS_FIRST = 0x80      # first (and, without a continuation, only) fragment
ODS_LAST = 0x40       # continuation fragment
ODS_SINGLE = 0xC0     # whole object in one segment
# Composition object flags.
OBJ_CROPPED = 0x80
OBJ_FORCED = 0x40

CLOCK_HZ = 90000      # PTS/DTS are 90 kHz presentation time stamps


class SupError(ValueError):
    pass


def seconds(pts):
    """Convert a 90 kHz time stamp to seconds as an exact fraction string."""
    return str(Fraction(pts, CLOCK_HZ))


def rle_pixels(data, width, height, alpha):
    """Decode the run-length coded bitmap and count pixels whose palette entry is not transparent.

    ``alpha`` maps palette entry id to its alpha byte; entries it does not
    define are treated as fully transparent.
    """
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
    # State of the current epoch.
    objects = {}                 # object id -> assembled width/height/bitmap
    palettes = {}                # palette id -> {entry id: [Y, Cb, Cr, alpha]}
    windows = []                 # latest window definition segment
    active = []                  # subtitles currently on screen
    out = {"width": None, "height": None, "display_sets": 0, "epochs": 0,
           "subtitles": [], "palettes": {}}
    pcs = None
    pending = None               # object definition being assembled

    def finish_pending():
        if pending is not None:
            objects[pending["id"]] = pending

    while pos < n:
        if pos + 13 > n or data[pos:pos + 2] != b"PG":
            raise SupError("bad segment header at %d" % pos)
        pts, _dts, kind, size = struct.unpack_from(">IIBH", data, pos + 2)
        end = pos + 13 + size
        if end > n:
            raise SupError("truncated segment at %d" % pos)
        body = data[pos + 13:end]
        pos = end

        if kind == PCS:
            if len(body) < 11:
                raise SupError("truncated PCS at %d" % pts)
            w, h, _rate, _number, state, pal_update, pal_id, count = \
                struct.unpack_from(">HHBHBBBB", body)
            if out["width"] is None:
                out["width"], out["height"] = w, h
            epoch_start = bool(state & 0x80)
            if epoch_start:
                out["epochs"] += 1
                # A new epoch discards every object and palette of the old one,
                # including any fragment that was still being assembled.
                objects, palettes, windows, active, pending = {}, {}, [], [], None
            else:
                finish_pending()
            comps, q = [], 11
            for _ in range(count):
                if q + 8 > len(body):
                    raise SupError("truncated composition object at %d" % pts)
                oid, wid, flags, x, y = struct.unpack_from(">HBBHH", body, q)
                q += 8
                crop = None
                if flags & OBJ_CROPPED:
                    if q + 8 > len(body):
                        raise SupError("truncated crop rectangle at %d" % pts)
                    cx, cy, cw, ch = struct.unpack_from(">HHHH", body, q)
                    q += 8
                    crop = {"x": cx, "y": cy, "width": cw, "height": ch}
                comps.append({"id": oid, "window": wid, "flags": flags,
                              "x": x, "y": y, "crop": crop})
            pcs = {"pts": pts, "palette_id": pal_id,
                   "palette_only": bool(pal_update & 0x80), "comps": comps}
            out["display_sets"] += 1

        elif kind == WDS:
            defs, q = [], 1
            count = body[0] if body else 0
            for _ in range(count):
                if q + 9 > len(body):
                    raise SupError("truncated WDS at %d" % pts)
                wid, wx, wy, ww, wh = struct.unpack_from(">BHHHH", body, q)
                q += 9
                defs.append({"id": wid, "x": wx, "y": wy, "width": ww, "height": wh})
            windows = defs

        elif kind == PDS:
            if len(body) < 2:
                raise SupError("truncated PDS at %d" % pts)
            pal_id, _version = body[0], body[1]
            table = palettes.setdefault(pal_id, {})
            q = 2
            while q + 4 < len(body):
                entry, yv, cb, cr, alpha = struct.unpack_from(">BBBBB", body, q)
                table[entry] = [yv, cb, cr, alpha]
                q += 5

        elif kind == ODS:
            if len(body) < 4:
                raise SupError("truncated ODS at %d" % pts)
            oid, _version, sequence = struct.unpack_from(">HBB", body, 0)
            if sequence in (ODS_FIRST, ODS_SINGLE):
                if len(body) < 11:
                    raise SupError("truncated ODS object at %d" % pts)
                width, height = struct.unpack_from(">HH", body, 7)
                pending = {"id": oid, "width": width, "height": height,
                           "data": bytearray(body[11:])}
            else:                       # continuation fragment (ODS_LAST, 0x40)
                if pending is None or pending["id"] != oid:
                    raise SupError("object fragment without a start at %d" % pts)
                pending["data"].extend(body[4:])
                if sequence == ODS_LAST:
                    finish_pending()
                    pending = None
            if sequence == ODS_SINGLE:
                finish_pending()
                pending = None

        elif kind == END and pcs is not None:
            palette = palettes.get(pcs["palette_id"], {})
            alpha = {entry: rgba[3] for entry, rgba in palette.items()}
            now = seconds(pcs["pts"])

            if pcs["palette_only"]:
                # A fade changes palette entries only: the same subtitle stays
                # on screen, so it must not become a new subtitle.
                for sub in active:
                    sub["palette_updates"].append(now)
            else:
                for sub in active:
                    sub["end"] = now
                active = []
                if pcs["comps"]:
                    objs, forced = [], False
                    for c in pcs["comps"]:
                        o = objects.get(c["id"])
                        if o is None:
                            raise SupError(
                                "composition uses undefined object %d" % c["id"])
                        if c["flags"] & OBJ_FORCED:
                            forced = True
                        objs.append({
                            "id": c["id"],
                            "x": c["x"],
                            "y": c["y"],
                            "width": o["width"],
                            "height": o["height"],
                            "opaque_pixels": rle_pixels(
                                bytes(o["data"]), o["width"], o["height"], alpha),
                            "window": c["window"],
                            "crop": c["crop"],
                        })
                    sub = {"start": now, "end": None, "forced": forced,
                           "objects": objs,
                           "windows": [dict(w) for w in windows],
                           "palette_updates": []}
                    out["subtitles"].append(sub)
                    active.append(sub)
            pcs = None

    # At the end of the stream the final state of every palette id is the state
    # of the last epoch; key ids as strings for JSON.
    out["palettes"] = {
        str(pal_id): {str(entry): list(rgba) for entry, rgba in table.items()}
        for pal_id, table in palettes.items()
    }
    return out

