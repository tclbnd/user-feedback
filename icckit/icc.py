"""ICC colour profiles (.icc, .icm), versions 2 and 4: header, tag table and the common tag types."""
import struct

CLASSES = {b"scnr": "input", b"mntr": "display", b"prtr": "output", b"link": "device_link",
           b"spac": "color_space", b"abst": "abstract", b"nmcl": "named_color"}
INTENTS = ["perceptual", "relative_colorimetric", "saturation", "absolute_colorimetric"]


class IccError(ValueError):
    pass


def s15(data, p, n=1):
    return [v / 65536 for v in struct.unpack_from(">%dI" % n, data, p)]


def fmt(vals):
    return ["%.4f" % v for v in vals]


def text(data, p, size):
    kind = data[p:p + 4]
    if kind == b"desc":
        n = struct.unpack_from(">I", data, p + 8)[0]
        return data[p + 12:p + 12 + n].split(b"\0")[0].decode("latin-1")
    if kind == b"text":
        return data[p + 8:p + size].split(b"\0")[0].decode("latin-1")
    return None


def curve(data, p):
    kind = data[p:p + 4]
    if kind == b"curv":
        n = struct.unpack_from(">I", data, p + 8)[0]
        if n == 0:
            return "identity"
        return "table %d" % n
    raise IccError("not a curve tag type %r" % kind)


def parse(data):
    if len(data) < 132 or data[36:40] != b"acsp":
        raise IccError("not an ICC profile")
    size, cmm, version = struct.unpack_from(">I4sI", data, 0)
    cls, space, pcs = data[12:16], data[16:20], data[20:24]
    intent = struct.unpack_from(">I", data, 64)[0]
    count = struct.unpack_from(">I", data, 128)[0]
    tags, seen, shared = {}, set(), 0
    for i in range(count):
        sig, off, tsize = struct.unpack_from(">4sII", data, 132 + 12 * i)
        if (off, tsize) in seen:
            shared += 1
        seen.add((off, tsize))
        tags[sig] = (off, tsize)
    out = {"version": "%d.%d" % (version >> 24, version >> 16 & 255),
           "class": CLASSES.get(cls, cls.decode("latin-1")), "color_space": space.decode("latin-1").strip(),
           "pcs": pcs.decode("latin-1").strip(), "intent": INTENTS[intent] if intent < 4 else "intent_%d" % intent,
           "illuminant": fmt(s15(data, 68, 3)), "size_ok": size == len(data), "tags": count, "shared_tags": shared}

    def tag(sig, fn):
        if sig not in tags:
            return None
        off, tsize = tags[sig]
        return fn(data, off, tsize) if fn is text else fn(data, off)

    out["description"] = tag(b"desc", text)
    out["copyright"] = tag(b"cprt", text)
    out["white_point"] = tag(b"wtpt", lambda d, p: fmt(s15(d, p + 8, 3)))
    out["chad"] = tag(b"chad", lambda d, p: fmt(s15(d, p + 8, 9)))
    trc = {k: tag(s, curve) for k, s in (("red", b"rTRC"), ("green", b"gTRC"), ("blue", b"bTRC"), ("gray", b"kTRC"))}
    out["trc"] = {k: v for k, v in trc.items() if v is not None}
    return out
