"""CAF file header and chunk walk."""


class CafError(ValueError):
    pass


def chunks(data):
    """Return a list of ``(chunk_type, body)`` in file order."""
    if len(data) < 8 or data[:4] != b"caff":
        raise CafError("not a CAF file")
    if int.from_bytes(data[4:6], "big") != 1:
        raise CafError("unsupported CAF version")
    pos, out = 8, []
    while pos + 12 <= len(data):
        kind = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 12], "big")
        start = pos + 12
        if start + size > len(data):
            raise CafError("truncated %s chunk" % kind.decode("latin-1"))
        out.append((kind, data[start:start + size]))
        pos = start + size
    return out
