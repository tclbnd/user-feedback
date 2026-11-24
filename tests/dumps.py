"""Tiny YM dumps for the tests, built the way Leonard's converters lay them out."""

import struct


def dump(title=b"Test"):
    head = b"YM6!LeOnArD!" + bytes(22) + title + b"\0Me\0\0"
    return head + bytes(16 * 0) + b"End!"


def frames_data(frames, regs, interleaved=False):
    """Encode frames (a list of register lists) register-by-register or frame-by-frame."""
    if interleaved:
        return bytes(frame[r] for r in range(regs) for frame in frames)
    return bytes(value for frame in frames for value in frame)


def ym5(frames, rate=50, clock=2000000, loop=0, interleaved=False, drums=(), title=b"T", author=b"A",
        comment=b"", extra=b"", magic=b"YM5!"):
    """A YM5/YM6 file: fixed big-endian header, digidrum sizes+data, strings, registers, End!."""
    regs = 16
    attrs = 1 if interleaved else 0
    head = magic + b"LeOnArD!" + struct.pack(">IIHIHIH", len(frames), attrs, len(drums), clock, rate, loop, len(extra))
    body = extra + b"".join(struct.pack(">I", len(d)) + d for d in drums)
    body += title + b"\0" + author + b"\0" + comment + b"\0"
    return head + body + frames_data(frames, regs, interleaved) + b"End!"


def ym3(frames, loop=None, magic=b"YM3!"):
    """YM3 (or YM3b when loop is given) has no header, 14 registers per frame."""
    data = magic + frames_data(frames, 14)
    return data if loop is None else data + struct.pack("<I", loop)
