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
CONTAINERS = {b"moov", b"udta", b"ilst", b"trak", b"mdia", b"minf"}


class Mp4Error(ValueError):
    pass


def boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        head = 8
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


def _data_values(data, b, e):
    out = []
    for k, db, de in boxes(data, b, e):
        if k == b"data":
            dtype = int.from_bytes(data[db + 1:db + 4], "big")
            out.append((dtype, data[db + 8:de]))
    return out


def _value(key, dtype, raw):
    if key == "gnre":
        i = int.from_bytes(raw[:2], "big")
        return GENRES[i] if 0 <= i < len(GENRES) else None
    if dtype in (1, 2):
        return raw.decode("latin-1")
    if dtype in (13, 14):
        return {"format": "jpeg" if dtype == 13 else "png", "bytes": len(raw)}
    if dtype in (21, 22):
        return int.from_bytes(raw, "big")
    return raw.hex()


def _ilst(data, b, e):
    tags = {}
    for kind, ib, ie in boxes(data, b, e):
        key = "©gen" if kind == b"gnre" else _key(kind)
        vals = [_value(_key(kind), t, raw) for t, raw in _data_values(data, ib, ie)]
        if vals:
            tags[key] = vals[0]
    return tags


def _meta(data, b, e):
    b += 4                                           # version/flags
    hit = find(data, b, e, b"ilst")
    return _ilst(data, *hit) if hit else {}


def _chpl(data, b, e):
    p = b + 8
    count = data[p]
    p += 1
    out = []
    for _ in range(count):
        start, = struct.unpack_from(">Q", data, p)
        n = data[p + 8]
        out.append({"start": str(Fraction(start, 1000)), "title": data[p + 9:p + 9 + n].decode("utf-8", "replace")})
        p += 9 + n
    return out


def parse(data):
    moov = find(data, 0, len(data), b"moov")
    if not moov:
        raise Mp4Error("no moov box")
    out = {"timescale": None, "duration": None, "tags": {}, "chapters": []}
    _walk(data, *moov, out)
    return out


def _walk(data, start, end, out):
    for kind, b, e in boxes(data, start, end):
        if kind == b"mvhd":
            scale, dur = struct.unpack_from(">II", data, b + 12)
            out["timescale"] = scale
            out["duration"] = str(Fraction(dur, scale)) if scale else None
        elif kind == b"udta":
            for k, ub, ue in boxes(data, b, e):
                if k == b"meta":
                    out["tags"].update(_meta(data, ub, ue))
                elif k == b"chpl":
                    out["chapters"] = _chpl(data, ub, ue)
        elif kind in CONTAINERS:
            _walk(data, b, e, out)
