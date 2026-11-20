"""IFF chunk walking for AIFF / AIFF-C files."""


class AiffError(ValueError):
    pass


def form(data):
    """Return ``(form_type, [(chunk_id, body), ...])``."""
    if len(data) < 12 or data[:4] != b"FORM":
        raise AiffError("not an IFF FORM file")
    form_type = data[8:12]
    if form_type not in (b"AIFF", b"AIFC"):
        raise AiffError("unsupported form type %r" % form_type)
    end = min(len(data), 8 + int.from_bytes(data[4:8], "big"))
    pos, out = 12, []
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        out.append((cid, data[pos + 8:pos + 8 + size]))
        # Chunks are padded to an even byte count; the pad byte is not
        # included in the chunk size.
        pos += 8 + size + (size & 1)
    return form_type, out
