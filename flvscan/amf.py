"""AMF0 values used by onMetaData."""
import struct


class AmfError(ValueError):
    pass


def _string(b, p):
    n = int.from_bytes(b[p:p + 2], "big")
    return b[p + 2:p + 2 + n].decode("utf-8", "replace"), p + 2 + n


def value(b, p):
    t = b[p]
    p += 1
    if t == 0:
        return struct.unpack(">d", b[p:p + 8])[0], p + 8
    if t == 1:
        return b[p] != 0, p + 1
    if t == 2:
        return _string(b, p)
    if t in (3, 8):
        if t == 8:
            p += 4
        out = {}
        while True:
            key, p = _string(b, p)
            if not key:
                return out, p + 1
            out[key], p = value(b, p)
    if t == 5:
        return None, p
    raise AmfError(f"unsupported AMF0 type {t}")
