"""MP4 / M4A movie metadata: mvhd duration, iTunes item list (moov/udta/meta/ilst) and Nero chapters (chpl)."""
import struct
from fractions import Fraction

GENRES = ("Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal",
          "New Age", "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial",
          "Alternative", "Ska", "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop",
          "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental", "Acid", "House", "Game",
          "Sound Clip", "Gospel", "Noise", "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative",
          "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial",
          "Electronic", "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta",
          "Top 40", "Christian Rap", "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
          "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk", "Acid Jazz", "Polka",
          "Retro", "Musical", "Rock & Roll", "Hard Rock")

# iTunes data box type codes.
TEXT_TYPES = frozenset((1, 2))          # UTF-8, UTF-16
INT_TYPES = frozenset((3, 4, 5, 21, 22, 23, 24, 25))
IMAGE_TYPES = frozenset((13, 14))       # JPEG, PNG

# Nero chapter timestamps are stored in 100-nanosecond units.
CHPL_SCALE = 10_000_000


class Mp4Error(ValueError):
    pass


def boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        head = 8
        if size == 1:                                  # 64-bit largesize
            if pos + 16 > end:
                raise Mp4Error("box %r at %d has a bad size" % (kind, pos))
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            head = 16
        elif size == 0:                                # box runs to end of its parent
            size = end - pos
        if size < head or pos + size > end:
            raise Mp4Error("box %r at %d has a bad size" % (kind, pos))
        yield kind, pos + head, pos + size
        pos += size


def find(data, start, end, kind):
    for k, b, e in boxes(data, start, end):
        if k == kind:
            return b, e
    return None


def _key(kind):
    return kind.decode("latin-1")


def _decode_text(dtype, raw):
    # iTunes writes type 1 as UTF-8 and type 2 as UTF-16 (big endian unless a BOM says otherwise).
    if dtype == 2:
        codec = "utf-16" if raw[:2] in (b"\xfe\xff", b"\xff\xfe") else "utf-16-be"
    else:
        codec = "utf-8"
    try:
        return raw.decode(codec)
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _data_values(data, b, e):
    out = []
    for k, db, de in boxes(data, b, e):
        if k == b"data":
            dtype = int.from_bytes(data[db + 1:db + 4], "big")
            out.append((dtype, data[db + 8:de]))
    return out


def _value(kind, dtype, raw):
    if kind == b"gnre":
        # The gnre payload holds a 1-based ID3v1 genre index.
        i = int.from_bytes(raw[:2], "big") - 1
        return GENRES[i] if 0 <= i < len(GENRES) else None
    if kind in (b"trkn", b"disk"):
        # uint16 reserved, uint16 number, uint16 total (a zero total means unset).
        number = int.from_bytes(raw[2:4], "big") if len(raw) >= 4 else 0
        total = int.from_bytes(raw[4:6], "big") if len(raw) >= 6 else 0
        return [number, total or None]
    if dtype in TEXT_TYPES:
        return _decode_text(dtype, raw)
    if dtype in IMAGE_TYPES:
        return {"format": "jpeg" if dtype == 13 else "png", "bytes": len(raw)}
    if dtype in INT_TYPES:
        return int.from_bytes(raw, "big")
    return raw.hex()


def _freeform(data, b, e):
    mean = name = ""
    vals = []
    for k, cb, ce in boxes(data, b, e):
        if k == b"mean":
            mean = _decode_text(1, data[cb + 4:ce])    # skip version/flags of the full box
        elif k == b"name":
            name = _decode_text(1, data[cb + 4:ce])
        elif k == b"data":
            dtype = int.from_bytes(data[cb + 1:cb + 4], "big")
            vals.append(_value(b"----", dtype, data[cb + 8:ce]))
    return mean + ":" + name, vals


def _ilst(data, b, e):
    tags = {}
    for kind, ib, ie in boxes(data, b, e):
        if kind == b"----":
            key, vals = _freeform(data, ib, ie)
        else:
            key = "©gen" if kind == b"gnre" else _key(kind)
            vals = [v for v in (_value(kind, t, raw) for t, raw in _data_values(data, ib, ie))
                    if v is not None]
        if not vals:
            continue
        if key == "covr":                               # one entry per picture
            tags.setdefault(key, []).extend(vals)
        else:
            tags[key] = vals[0]
    return tags


def _meta(data, b, e):
    b += 4                                           # version/flags
    hit = find(data, b, e, b"ilst")
    return _ilst(data, *hit) if hit else {}


def _chpl(data, b, e):
    p = b + 4                                        # version/flags
    p += 4                                           # Nero reserved bytes
    count = data[p]
    p += 1
    out = []
    for _ in range(count):
        start, = struct.unpack_from(">Q", data, p)
        n = data[p + 8]
        out.append({"start": str(Fraction(start, CHPL_SCALE)),
                    "title": data[p + 9:p + 9 + n].decode("utf-8", "replace")})
        p += 9 + n
    return out


def _mvhd(data, b):
    if data[b] == 1:                                 # version 1: 64-bit creation/modification times
        timescale = struct.unpack_from(">I", data, b + 20)[0]
        duration = struct.unpack_from(">Q", data, b + 24)[0]
    else:                                            # version 0: 32-bit times, timescale/duration at +12
        timescale, duration = struct.unpack_from(">II", data, b + 12)
    return timescale, duration


def parse(data):
    moov = find(data, 0, len(data), b"moov")
    if not moov:
        raise Mp4Error("no moov box")
    out = {"timescale": None, "duration": None, "tags": {}, "chapters": []}
    # Only the movie-level moov box carries file-wide iTunes metadata; never descend into tracks.
    for kind, b, e in boxes(data, *moov):
        if kind == b"mvhd":
            scale, dur = _mvhd(data, b)
            out["timescale"] = scale
            out["duration"] = str(Fraction(dur, scale)) if scale else None
        elif kind == b"udta":
            for k, ub, ue in boxes(data, b, e):
                if k == b"meta":
                    out["tags"].update(_meta(data, ub, ue))
                elif k == b"chpl":
                    out["chapters"] = _chpl(data, ub, ue)
    return out
