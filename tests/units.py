"""Small TrueHD streams for the tests."""
import struct


def sync(info=0x0001E000):
    return struct.pack(">II", 0xF8726FBA, info) + struct.pack(">HHHH", 0xB752, 0, 0, 1000) + bytes([0x20]) + bytes(11)


def unit(words, with_sync=False):
    body = sync() if with_sync else b""
    body += bytes(words * 2 - 4 - len(body))
    return struct.pack(">HH", 0xA000 | words, 0) + body
