"""Small PGS streams for the tests."""
import struct


def seg(kind, body, pts=0):
    return b"PG" + struct.pack(">IIBH", pts, 0, kind, len(body)) + body


def subtitle(pts, w=100, h=2):
    pcs = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, 0, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, 10, 20)
    pds = bytes([0, 0]) + bytes([1, 235, 128, 128, 255])
    picture = (bytes([0, 0x80 | 50, 1]) + bytes([0, 50]) + b"\x00\x00") * h
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (4 + len(picture)).to_bytes(3, "big") + struct.pack(">HH", w, h) + picture
    return seg(0x16, pcs, pts) + seg(0x14, pds, pts) + seg(0x15, ods, pts) + seg(0x80, b"", pts)
