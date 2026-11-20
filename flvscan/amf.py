"""AMF0 values used by onMetaData.

Covers the AMF0 data types commonly written by streaming software
(Adobe AMF 0 specification, section 2.12): Number, Boolean, String,
Object, Null, ECMA Array, Strict Array, Date and Long String.
"""
import struct

# AMF0 type markers (AMF 0 spec, table of type markers).
AMF_NUMBER = 0
AMF_BOOLEAN = 1
AMF_STRING = 2
AMF_OBJECT = 3
AMF_NULL = 5
AMF_UNDEFINED = 6
AMF_ECMA_ARRAY = 8
AMF_OBJECT_END = 9
AMF_STRICT_ARRAY = 10
AMF_DATE = 11
AMF_LONG_STRING = 12


class AmfError(ValueError):
    pass


def _take(b, p, n, what):
    if p + n > len(b):
        raise AmfError("truncated AMF0 %s" % what)
    return b[p:p + n], p + n


def _u16(b, p):
    chunk, p = _take(b, p, 2, "length")
    return int.from_bytes(chunk, "big"), p


def _u32(b, p):
    chunk, p = _take(b, p, 4, "length")
    return int.from_bytes(chunk, "big"), p


def _string(b, p):
    n, p = _u16(b, p)
    chunk, p = _take(b, p, n, "string")
    return chunk.decode("utf-8", "replace"), p


def _long_string(b, p):
    n, p = _u32(b, p)
    chunk, p = _take(b, p, n, "long string")
    return chunk.decode("utf-8", "replace"), p


def _object_end(b, p):
    """Consume the single AMF_OBJECT_END marker following an empty key."""
    chunk, p = _take(b, p, 1, "object end")
    if chunk[0] != AMF_OBJECT_END:
        raise AmfError("expected AMF0 object end, got marker %d" % chunk[0])
    return p


def _object(b, p):
    """Parse an associative Object (type 3) body.

    An object terminates with an empty UTF-8 key followed by an
    AMF_OBJECT_END marker.
    """
    out = {}
    while True:
        key, p = _string(b, p)
        if not key:
            return out, _object_end(b, p)
        out[key], p = value(b, p)


def _ecma_array(b, p):
    """Parse an ECMA Array (type 8): an associative count then object body."""
    _count, p = _u32(b, p)  # associative count is informational
    return _object(b, p)


def _strict_array(b, p):
    """Parse a Strict Array (type 10): a dense, index-based array."""
    count, p = _u32(b, p)
    out = []
    for _ in range(count):
        item, p = value(b, p)
        out.append(item)
    return out, p


def _date(b, p):
    """Parse a Date (type 11): epoch milliseconds plus a UTC offset."""
    chunk, p = _take(b, p, 10, "date")
    millis = struct.unpack(">d", chunk[:8])[0]
    # chunk[8:10] is the 16-bit, minutes-relative-to-S16 local time offset.
    return millis, p


def value(b, p):
    """Read one AMF0 value at *p* and return (value, new_position)."""
    if p >= len(b):
        raise AmfError("missing AMF0 type marker")
    t = b[p]
    p += 1
    if t == AMF_NUMBER:
        chunk, p = _take(b, p, 8, "number")
        return struct.unpack(">d", chunk)[0], p
    if t == AMF_BOOLEAN:
        chunk, p = _take(b, p, 1, "boolean")
        return chunk[0] != 0, p
    if t == AMF_STRING:
        return _string(b, p)
    if t == AMF_LONG_STRING:
        return _long_string(b, p)
    if t == AMF_OBJECT:
        return _object(b, p)
    if t == AMF_ECMA_ARRAY:
        return _ecma_array(b, p)
    if t == AMF_STRICT_ARRAY:
        return _strict_array(b, p)
    if t == AMF_DATE:
        return _date(b, p)
    if t in (AMF_NULL, AMF_UNDEFINED):
        return None, p
    raise AmfError("unsupported AMF0 type %d" % t)

