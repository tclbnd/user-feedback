"""A minimal MUS lump: one instrument, release events with short delays, score end."""
import struct


def lump():
    body = bytes([0x80, 60, 10, 0x80, 61, 10, 0x60])        # release ch0 note 60 +10 ticks, release note 61 +10, end
    return b"MUS\x1a" + struct.pack("<HHHHHH", len(body), 18, 1, 0, 1, 0) + struct.pack("<H", 30) + body
