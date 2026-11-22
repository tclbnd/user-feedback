"""Builders for small synthetic PSF files used by the tests."""
import struct
import zlib


def psf(version=1, program=bytes(range(256)) * 16, reserved=b"", tags=None):
    crc_bad, raw_tag, crlf = False, None, False
    comp = zlib.compress(program, 9) if program else b""
    crc = zlib.crc32(comp) & 0xFFFFFFFF
    if crc_bad:
        crc ^= 1
    out = b"PSF" + bytes([version]) + struct.pack("<III", len(reserved), len(comp), crc) + reserved + comp
    if raw_tag is not None:
        out += b"[TAG]" + raw_tag
    elif tags:
        nl = "\r\n" if crlf else "\n"
        out += b"[TAG]" + nl.join("%s=%s" % kv for kv in tags).encode("utf-8")
    return out
