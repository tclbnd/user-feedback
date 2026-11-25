"""A simple lossy WebP for the tests."""
import struct


def still(w=64, h=48):
    vp8 = bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", w, h) + bytes(40)
    body = b"WEBP" + b"VP8 " + struct.pack("<I", len(vp8)) + vp8
    return b"RIFF" + struct.pack("<I", len(body)) + body
