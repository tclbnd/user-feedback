"""ISO base media file format boxes (ISO/IEC 14496-12)."""
import struct


class Mp4Error(ValueError):
    pass


def boxes(data, start=0, end=None):
    """Yield (type, payload_start, payload_end) for the boxes in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        head = 8
        if size < head or pos + size > end:
            raise Mp4Error("bad size for box %r" % kind)
        if kind == b"uuid":
            head += 16
        yield kind.decode("latin-1"), pos + head, pos + size
        pos += size


def find(data, start, end, kind):
    for k, s, e in boxes(data, start, end):
        if k == kind:
            return s, e
    return None


def path(data, start, end, *kinds):
    for kind in kinds:
        hit = find(data, start, end, kind)
        if hit is None:
            return None
        start, end = hit
    return start, end


def full(data, start):
    return data[start], int.from_bytes(data[start + 1:start + 4], "big"), start + 4
