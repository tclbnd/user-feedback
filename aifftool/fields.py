"""Decoding of COMM, SSND and MARK chunk contents."""
from .chunks import AiffError


def u16(b, p):
    return int.from_bytes(b[p:p + 2], "big")


def u32(b, p):
    return int.from_bytes(b[p:p + 4], "big")


def sample_rate(ext):
    """Sample rate stored as an 80-bit IEEE 754 extended float."""
    if len(ext) < 10:
        raise AiffError("short extended sample rate")
    first = u16(ext, 0)
    sign = -1 if first & 0x8000 else 1
    exp = first & 0x7FFF
    mant = int.from_bytes(ext[2:10], "big")
    if exp == 0 and mant == 0:
        return 0
    if exp == 0x7FFF:
        raise AiffError("invalid extended sample rate")
    value = sign * mant * 2.0 ** (exp - 16383 - 63)
    as_int = int(value)
    return as_int if value == as_int else value


def comm(body, aifc):
    if len(body) < 18:
        raise AiffError("short COMM chunk")
    out = {
        "channels": u16(body, 0),
        "frames": u32(body, 2),
        "bits": u16(body, 6),
        "sample_rate": sample_rate(body[8:18]),
        "compression": "NONE",
        "compression_name": None,
    }
    if aifc:
        if len(body) < 22:
            raise AiffError("short AIFF-C COMM chunk")
        out["compression"] = body[18:22].decode("latin-1")
        if len(body) > 22:
            out["compression_name"], _ = pstring(body, 22)
        else:
            out["compression_name"] = ""
    return out


def pstring(b, p):
    """Read a Pascal string; return ``(text, next_position)``.

    The count byte plus the text are padded to an even byte count.
    """
    if p >= len(b):
        raise AiffError("short Pascal string")
    n = b[p]
    text = b[p + 1:p + 1 + n].decode("latin-1")
    size = 1 + n
    return text, p + size + (size & 1)


def markers(body):
    if len(body) < 2:
        raise AiffError("short MARK chunk")
    count = u16(body, 0)
    p, out = 2, []
    for _ in range(count):
        if p + 6 > len(body):
            raise AiffError("short MARK chunk")
        mid = u16(body, p)
        pos = u32(body, p + 2)
        name, p = pstring(body, p + 6)
        out.append({"id": mid, "position": pos, "name": name})
    return out


def ssnd(body):
    """Split an SSND body into ``(offset, block_size, sound_data)``."""
    if len(body) < 8:
        raise AiffError("short SSND chunk")
    offset = u32(body, 0)
    block_size = u32(body, 4)
    return offset, block_size, body[8 + offset:]
