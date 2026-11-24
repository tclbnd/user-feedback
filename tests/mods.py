"""Builders for small IT modules used by the tests."""
import struct


def minimal(title=b"Test"):
    h = bytearray(192)
    h[:4] = b"IMPM"
    h[4:4 + len(title)] = title
    h[48:53] = bytes([128, 48, 6, 125, 128])
    return bytes(h)


def _sample_header(flags=0x01, length=0, c5=8363, name=b""):
    s = bytearray(80)
    s[0:4] = b"IMPS"
    s[18] = flags
    s[19] = 64
    s[20:20 + len(name)] = name
    struct.pack_into("<I", s, 48, length)
    struct.pack_into("<I", s, 60, c5)
    return s


def module(orders=(), samples=(), patterns=(), pans=None, cwt=0x0214, cmwt=0x0200,
           title=b"Test", flags=0x0d, special=0, gv=128, mv=48, speed=6, tempo=125):
    """Build an IT module.

    ``samples`` is a list of ``(flags, length)`` pairs.  ``patterns`` is a
    list of row counts, or ``None`` for a pattern that is not stored.
    """
    ordnum, smpnum, patnum = len(orders), len(samples), len(patterns)
    tbl = 192 + ordnum + 4 * smpnum + 4 * patnum
    smp_off = [tbl + 80 * i for i in range(smpnum)]
    pat_base = tbl + 80 * smpnum
    pat_off = []
    stored = 0
    for rows in patterns:
        if rows is None:
            pat_off.append(0)
        else:
            pat_off.append(pat_base + 8 * stored)
            stored += 1

    h = bytearray(192)
    h[:4] = b"IMPM"
    h[4:4 + len(title)] = title
    struct.pack_into("<8H", h, 32, ordnum, 0, smpnum, patnum, cwt, cmwt, flags, special)
    h[48:53] = bytes([gv, mv, speed, tempo, 128])
    if pans:
        h[64:64 + len(pans)] = bytes(pans)

    body = bytearray(orders)
    body += b"".join(struct.pack("<I", o) for o in smp_off)
    body += b"".join(struct.pack("<I", o) for o in pat_off)
    for fl, length in samples:
        body += _sample_header(fl, length)
    for rows in patterns:
        if rows is not None:
            body += struct.pack("<HHI", 8, rows, 0)
    return bytes(h) + bytes(body)
