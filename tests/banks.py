"""A minimal DLS bank: one instrument, one region, one wave."""
import struct


def ck(cid, body):
    return cid + struct.pack("<I", len(body)) + body


def bank(frames=100):
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    wave = ck(b"LIST", b"wave" + ck(b"fmt ", fmt) + ck(b"data", bytes(frames * 2)))
    rgn = ck(b"LIST", b"rgn " + ck(b"rgnh", struct.pack("<6H", 0, 127, 0, 127, 0, 0)) + ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, 0)))
    ins = ck(b"LIST", b"ins " + ck(b"insh", struct.pack("<III", 1, 0, 5)) + ck(b"LIST", b"lrgn" + rgn))
    body = b"DLS " + ck(b"colh", struct.pack("<I", 1)) + ck(b"LIST", b"lins" + ins)
    body += ck(b"ptbl", struct.pack("<III", 8, 1, 0)) + ck(b"LIST", b"wvpl" + wave)
    return b"RIFF" + struct.pack("<I", len(body)) + body
