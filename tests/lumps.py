"""Builders for minimal MUS lumps used by the tests."""
import struct


def build(body, primary=1, secondary=0, instruments=(30,), tail=b""):
    """Wrap score bytes in a MUS header; `tail` is appended outside the score."""
    header = b"MUS\x1a" + struct.pack("<HHHHHH", len(body), 16 + 2 * len(instruments),
                                      primary, secondary, len(instruments), 0)
    return header + b"".join(struct.pack("<H", n) for n in instruments) + body + tail


def lump():
    body = bytes([0x80, 60, 10, 0x80, 61, 10, 0x60])        # release ch0 note 60 +10 ticks, release note 61 +10, end
    return build(body)
