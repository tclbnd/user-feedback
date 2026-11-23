"""Small .au files for the tests."""
import struct


def au(encoding, body, rate=8000, channels=1):
    return struct.pack(">4sIIIII", b".snd", 24, len(body), encoding, rate, channels) + body
