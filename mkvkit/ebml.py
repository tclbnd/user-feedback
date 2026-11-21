"""EBML elements (RFC 8794)."""


class EbmlError(ValueError):
    pass


def vint(data, pos, keep_marker=False):
    """Return (value, length, unknown).

    With ``keep_marker`` the marker bit is preserved, which is how element
    IDs are encoded.  Otherwise an all-ones payload is the "unknown data
    size" value of RFC 8794 section 4.3 (returned as ``unknown=True``).
    """
    if pos >= len(data):
        raise EbmlError("truncated element")
    first = data[pos]
    if first == 0:
        raise EbmlError("invalid variable-length integer")
    n = 1
    while n <= 8 and not first & (0x80 >> (n - 1)):
        n += 1
    if n > 8:
        raise EbmlError("invalid variable-length integer")
    if pos + n > len(data):
        raise EbmlError("truncated element")
    v = first if keep_marker else first & (0xFF >> n)
    for b in data[pos + 1:pos + n]:
        v = (v << 8) | b
    unknown = False if keep_marker else v == (1 << (7 * n)) - 1
    return v, n, unknown


def element(data, pos):
    """(id, payload_start, size or None for unknown)."""
    eid, n, _ = vint(data, pos, keep_marker=True)
    size, m, unknown = vint(data, pos + n)
    return eid, pos + n + m, None if unknown else size


def uint(b):
    return int.from_bytes(b, "big")
