"""FLV header and tags."""
from collections import namedtuple

Tag = namedtuple("Tag", "type timestamp body offset")


class FlvError(ValueError):
    pass


def header(data):
    if data[:3] != b"FLV":
        raise FlvError("not an FLV file")
    flags = data[4]
    return {"has_audio": bool(flags & 4), "has_video": bool(flags & 1)}, 9


def tags(data):
    info, pos = header(data)
    pos += 4
    out = []
    while pos + 11 <= len(data):
        kind = data[pos] & 0x1F
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        ts = int.from_bytes(data[pos + 4:pos + 7], "big")
        out.append(Tag(kind, ts, data[pos + 11:pos + 11 + size], pos))
        pos += 11 + size + 4
    return info, out
