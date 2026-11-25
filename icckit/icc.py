"""ICC colour profiles (.icc, .icm), versions 2 and 4: header, tag table and the common tag types."""
import struct

CLASSES = {b"scnr": "input", b"mntr": "display", b"prtr": "output", b"link": "device_link",
           b"spac": "color_space", b"abst": "abstract", b"nmcl": "named_color"}
INTENTS = ["perceptual", "relative_colorimetric", "saturation", "absolute_colorimetric"]


class IccError(ValueError):
    pass


def s15(data, p, n=1):
    """n s15Fixed16Numbers (signed, ICC.1 4.2.3)."""
    return [v / 65536 for v in struct.unpack_from(">%di" % n, data, p)]


def fmt(vals):
    return ["%.4f" % v for v in vals]


def text(data, p, size):
    kind = data[p:p + 4]
    if kind == b"desc":
        # v2 textDescriptionType: ASCII count includes the terminating null.
        n = struct.unpack_from(">I", data, p + 8)[0]
        return data[p + 12:p + 12 + n].split(b"\0")[0].decode("latin-1")
    if kind == b"text":
        return data[p + 8:p + size].split(b"\0")[0].decode("latin-1")
    if kind == b"mluc":
        # v4 multiLocalizedUnicodeType: records are (language, country, length, offset),
        # offsets relative to the start of the tag data, strings UTF-16BE.
        n, recsize = struct.unpack_from(">II", data, p + 8)
        if recsize < 12:
            raise IccError("bad mluc record size %d" % recsize)
        records = []
        for i in range(n):
            lang, country, length, offset = struct.unpack_from(">2s2sII", data, p + 16 + i * recsize)
            records.append((lang + country, length, offset))
        if not records:
            return None
        record = next((r for r in records if r[0] == b"enUS"), records[0])
        raw = data[p + record[2]:p + record[2] + record[1]]
        return raw.decode("utf-16-be").split("\0")[0]
    return None


def curve(data, p):
    kind = data[p:p + 4]
    if kind == b"curv":
        n = struct.unpack_from(">I", data, p + 8)[0]
        if n == 0:
            return "identity"
        if n == 1:
            # A single entry is a u8Fixed8 gamma value (ICC.1 4.2.4).
            return "gamma %.4f" % (struct.unpack_from(">H", data, p + 12)[0] / 256.0)
        return "table %d" % n
    if kind == b"para":
        # parametricCurveType: function type at p+8, parameters are s15Fixed16.
        ftype = struct.unpack_from(">H", data, p + 8)[0]
        nparams = {0: 1, 1: 3, 2: 4, 3: 5, 4: 7}.get(ftype)
        if nparams is None:
            raise IccError("unknown parametric curve type %d" % ftype)
        return "para %d: %s" % (ftype, " ".join(fmt(s15(data, p + 12, nparams))))
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
    # Profile version: byte 8 is the major version, byte 9 holds the minor
    # version in its high nibble and the bug-fix revision in its low nibble
    # (ICC.1 7.2.4).
    out = {"version": "%d.%d.%d" % (version >> 24, version >> 20 & 15, version >> 16 & 15),
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
