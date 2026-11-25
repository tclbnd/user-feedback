"""Tiny YM dumps for the tests, built field by field."""
import struct


def _cstr(text):
    return (text.encode("latin-1") if text else b"") + b"\0"


def modern(magic, frames, *, interleaved=True, clock=2000000, rate=50, loop=0,
           drums=(), title="Test", author="Me", comment="", extra=b"", regs=None):
    """Build a YM5!/YM6! dump. ``regs`` is a frames x 16 register matrix."""
    if regs is None:
        regs = [[0] * 16 for _ in range(frames)]
    attrs = 1 if interleaved else 0
    head = magic + b"LeOnArD!" + struct.pack(">IIHIHIH", frames, attrs, len(drums),
                                             clock, rate, loop, len(extra))
    body = head + extra
    for sample in drums:
        body += struct.pack(">I", len(sample)) + sample
    body += _cstr(title) + _cstr(author) + _cstr(comment)
    if interleaved:
        for r in range(16):
            body += bytes(regs[f][r] for f in range(frames))
    else:
        for f in range(frames):
            body += bytes(regs[f])
    body += b"End!"
    return body


def ym6(frames, **kwargs):
    return modern(b"YM6!", frames, **kwargs)


def ym5(frames, **kwargs):
    return modern(b"YM5!", frames, **kwargs)


def ym3(frames, *, loop=None):
    """Bare YM3 frames of 14 registers; append a LE32 loop frame for YM3b."""
    body = b"".join(bytes([f % 256] + [0] * 13) for f in range(frames))
    if loop is not None:
        body += struct.pack("<I", loop)
    return body


def dump(title=b"Test"):
    head = b"YM6!LeOnArD!" + bytes(22) + title + b"\0Me\0\0"
    return head + bytes(16 * 0) + b"End!"
