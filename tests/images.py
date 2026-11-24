"""A minimal GIF: no global colour table, one small image."""
import struct


def tiny(w=16, h=8, frames=1):
    head = b"GIF89a" + struct.pack("<HHBBB", w, h, 0x70, 0, 0)
    img = b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + b"\x08" + b"\x04abcd\x00"
    return head + img * frames + b"\x3b"
