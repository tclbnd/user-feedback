#!/usr/bin/env python3
"""Build demo.zaf: a stream with one corrupt payload and a truncated tail."""
import struct, zlib

def frame(seq, payload):
    body = struct.pack(">II", seq, len(payload)) + payload
    return struct.pack(">III", seq, len(payload), zlib.crc32(body)) + payload

out = b"ZQAF0001"
out += frame(0, b"\x00\x01" * 512)
out += frame(1, b"\x02\x03" * 400)
bad = bytearray(frame(2, b"\x04\x05" * 300))
bad[-1] ^= 0xFF  # payload bit flip
out += bytes(bad)
out += frame(3, b"\x06\x07" * 200)
out += frame(4, b"\x08\x09" * 100)[:40]  # truncated mid-frame
open("demo.zaf", "wb").write(out)
print("demo.zaf written")
