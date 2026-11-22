"""Builders for small synthetic PSF files used by the tests."""
import struct
import zlib


def psf(version=1, program=bytes(range(256)) * 16, reserved=b"", tags=None, raw_tag=None, crlf=False, crc_bad=False):
    comp = zlib.compress(program, 9) if program else b""
    crc = zlib.crc32(comp) & 0xFFFFFFFF
    if crc_bad:
        crc ^= 1
    if version == 2:
        # PSF2: 0x14-byte header (compressed size, decompressed size, CRC,
        # reserved size); reserved area then program, as in the PSF1 family.
        header = b"PSF" + bytes([version]) + struct.pack("<IIII", len(comp), len(program), crc, len(reserved))
    else:
        # PSF1 family: 16-byte header; reserved size, compressed size, CRC.
        header = b"PSF" + bytes([version]) + struct.pack("<III", len(reserved), len(comp), crc)
    out = header + reserved + comp
    if raw_tag is not None:
        out += b"[TAG]" + raw_tag
    elif tags:
        nl = "\r\n" if crlf else "\n"
        out += b"[TAG]" + nl.join("%s=%s" % kv for kv in tags).encode("utf-8")
    return out
