"""Page encoding and the page file."""
import os
import struct
import zlib

PAGE_SIZE, SLOTS, SLOT_SIZE, HEADER = 256, 4, 59, 18


def empty(pid):
    return {"id": pid, "lsn": 0, "slots": [""] * SLOTS}


def encode(page):
    body = b"".join(bytes([len(v.encode())]) + v.encode().ljust(SLOT_SIZE - 1, b"\0")
                    for v in page["slots"])
    raw = bytearray(b"PG" + struct.pack(">IQI", page["id"], page["lsn"], 0) + body)
    raw += b"\0" * (PAGE_SIZE - len(raw))
    raw[14:18] = struct.pack(">I", zlib.crc32(bytes(raw)))
    return bytes(raw)


def decode(raw, pid):
    if not raw or raw == b"\0" * PAGE_SIZE:
        return empty(pid)
    raw = raw.ljust(PAGE_SIZE, b"\0")
    _, lsn, _ = struct.unpack(">IQI", raw[2:18])
    slots = []
    for i in range(SLOTS):
        off = HEADER + i * SLOT_SIZE
        slots.append(raw[off + 1:off + 1 + raw[off]].decode("utf-8", "replace"))
    return {"id": pid, "lsn": lsn, "slots": slots}


def crc_of(raw):
    """CRC-32 over the full page with the checksum field (bytes 14-17) zeroed."""
    raw = raw.ljust(PAGE_SIZE, b"\0")
    return zlib.crc32(raw[:14] + b"\0" * 4 + raw[18:]) & 0xFFFFFFFF


def stored_crc(raw):
    raw = raw.ljust(PAGE_SIZE, b"\0")
    return struct.unpack(">I", raw[14:18])[0]


def verify(raw):
    """Classify a raw on-disk page.

    Returns "empty" for an all-zero (or missing) page, "torn" when the page
    header is present but the page is short or its CRC does not match a
    power-loss halfway write, and "ok" for an intact page.
    """
    if not raw or raw == b"\0" * PAGE_SIZE:
        return "empty"
    if len(raw) < PAGE_SIZE:
        return "torn"
    if raw[:2] != b"PG" or stored_crc(raw) != crc_of(raw):
        return "torn"
    return "ok"


class PageFile:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, "wb").close()

    def count(self):
        return (os.path.getsize(self.path) + PAGE_SIZE - 1) // PAGE_SIZE

    def read_raw(self, pid):
        with open(self.path, "rb") as fh:
            fh.seek(pid * PAGE_SIZE)
            return fh.read(PAGE_SIZE)

    def read(self, pid):
        return decode(self.read_raw(pid), pid)

    def write_raw(self, pid, raw):
        with open(self.path, "r+b") as fh:
            fh.seek(0, 2)
            if fh.tell() < pid * PAGE_SIZE:
                fh.write(b"\0" * (pid * PAGE_SIZE - fh.tell()))
            fh.seek(pid * PAGE_SIZE)
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
