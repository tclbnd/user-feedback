"""Tiny YM dumps for the tests, built header first, big-endian, like Leonard's converters."""


def dump(title=b"Test"):
    head = b"YM6!LeOnArD!" + bytes(22) + title + b"\0Me\0\0"
    return head + bytes(16 * 0) + b"End!"


def ym5(frames, attrs=0, drums=(), clock=2000000, rate=50, loop=0, extra=b"",
        title=b"", author=b"", comment=b"", regs=None, magic=b"YM6!", end=True):
    """Build a YM5!/YM6! file with ``frames`` register frames.

    ``regs`` is the flat register data: frame by frame when not interleaved,
    16 runs of ``frames`` values (register major) when ``attrs & 1``.
    It defaults to all zeros.
    """
    regs = list(regs) if regs is not None else [0] * (frames * 16)
    assert len(regs) == frames * 16
    head = magic + b"LeOnArD!" + (frames.to_bytes(4, "big") + attrs.to_bytes(4, "big")
                                  + len(drums).to_bytes(2, "big") + clock.to_bytes(4, "big")
                                  + rate.to_bytes(2, "big") + loop.to_bytes(4, "big")
                                  + len(extra).to_bytes(2, "big") + extra)
    parts = [head]
    for sample in drums:
        parts.append(len(sample).to_bytes(4, "big") + sample)
    parts.append(title + b"\0" + author + b"\0" + comment + b"\0")
    parts.append(bytes(regs))
    if end:
        parts.append(b"End!")
    return b"".join(parts)


def with_regs(count, **values):
    """Frame-major data for ``count`` frames, with register ``rN`` set to ``values["rN"]``."""
    out = []
    for _ in range(count):
        frame = [0] * 16
        for name, value in values.items():
            frame[int(name[1:])] = value
        out.extend(frame)
    return out


def interleave(regs):
    """Reorder frame-major register data into the register-major (interleaved) layout."""
    count = len(regs) // 16
    return [regs[f * 16 + r] for r in range(16) for f in range(count)]


def ym3(frames, regs=None, loop=None):
    """Build a YM3! (``loop is None``) or YM3b dump."""
    if regs is None:
        regs = [0] * (frames * 14)
    assert len(regs) == frames * 14
    data = b"YM3!" + bytes(regs)
    if loop is not None:
        data += loop.to_bytes(4, "little")
    return data
