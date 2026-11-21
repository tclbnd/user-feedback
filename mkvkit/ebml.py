"""EBML elements (RFC 8794)."""


class EbmlError(ValueError):
    pass


def vint(data, pos, keep_marker=False):
    """Return (value, length, unknown)."""
    if pos >= len(data):
        raise EbmlError("truncated element")
    first = data[pos]
    if first == 0:
        raise EbmlError("invalid variable-length integer")
    n = 1
    while not first & (0x80 >> (n - 1)):
        n += 1
    if pos + n > len(data):
        raise EbmlError("truncated element")
    v = first if keep_marker else first & (0xFF >> n)
    for b in data[pos + 1:pos + n]:
        v = (v << 8) | b
    return v, n, False


def element(data, pos):
    """(id, payload_start, size or None for unknown)."""
    eid, n, _ = vint(data, pos, keep_marker=True)
    size, m, _ = vint(data, pos + n)
    return eid, pos + n + m, size


def uint(b):
    return int.from_bytes(b, "big")
