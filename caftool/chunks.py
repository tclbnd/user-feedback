"""CAF file header and chunk walk.

The file header is ``caff`` followed by a big-endian UInt16 version and a
UInt16 flags field.  Each chunk header is a four byte type followed by a
big-endian signed 64 bit size.  A size of -1 means "the chunk runs to the end
of the file" (the ``data`` chunk uses this for recordings whose final length
is not known up front).
"""


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
        size = int.from_bytes(data[pos + 4:pos + 12], "big", signed=True)
        start = pos + 12
        if size == -1:
            # -1 means "to end of file"; the chunk is not truncated.
            out.append((kind, data[start:]))
            break
        if size < 0:
            raise CafError("bad %s chunk size" % kind.decode("latin-1"))
        end = start + size
        if end > len(data):
            raise CafError("truncated %s chunk" % kind.decode("latin-1"))
        out.append((kind, data[start:end]))
        pos = end
    return out
