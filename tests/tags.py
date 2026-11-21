import struct


def item(key, value, kind=0, read_only=False):
    if isinstance(value, str):
        value = value.encode("utf-8")
    flags = (kind << 1) | (1 if read_only else 0)
    return struct.pack("<II", len(value), flags) + key.encode("ascii") + b"\0" + value


def tag(items, version=2000, header=True, read_only=False):
    body = b"".join(items)
    size = len(body) + 32
    base = (1 if read_only else 0) | (0x80000000 if header and version >= 2000 else 0)
    footer = b"APETAGEX" + struct.pack("<IIII", version, size, len(items), base) + bytes(8)
    head = b""
    if header and version >= 2000:
        head = b"APETAGEX" + struct.pack("<IIII", version, size, len(items), base | 0x20000000) + bytes(8)
    return head + body + footer


def id3v1(title="Song"):
    return b"TAG" + title.encode("latin-1").ljust(30, b"\0") + bytes(95)


def audio(n=1000):
    return bytes((i * 31) & 0xFF for i in range(n))

