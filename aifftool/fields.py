"""Decoding of COMM, SSND and MARK chunk contents."""
from .chunks import AiffError


def u16(b, p):
    return int.from_bytes(b[p:p + 2], "big")


def u32(b, p):
    return int.from_bytes(b[p:p + 4], "big")


def sample_rate(ext):
    """Sample rate stored in the 10-byte extended field."""
    return int.from_bytes(ext[2:6], "big")


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
        out["compression"] = body[18:22].decode("latin-1")
    return out


def pstring(b, p):
    """Read a Pascal string; return ``(text, next_position)``."""
    n = b[p]
    text = b[p + 1:p + 1 + n].decode("latin-1")
    return text, p + 1 + n + (n % 2)


def markers(body):
    count = u16(body, 0)
    p, out = 2, []
    for _ in range(count):
        mid = u16(body, p)
        pos = u32(body, p + 2)
        name, p = pstring(body, p + 6)
        out.append({"id": mid, "position": pos, "name": name})
    return out
